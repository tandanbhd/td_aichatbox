# -*- coding: utf-8 -*-

# === Nhập các thư viện cần thiết ===
from flask import Flask, request, jsonify, render_template
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from docx import Document
from flask_cors import CORS
import google.generativeai as genai
import io
from google.oauth2 import service_account
import csv
import os
import json # Thêm thư viện json
import openai # Thêm thư viện OpenAI

# === Khởi tạo ứng dụng Flask ===
app = Flask(__name__)
CORS(app)

# === Cấu hình API ===

# --- Cấu hình OpenAI API ---
# Lấy API Key từ biến môi trường
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
openai.api_key = OPENAI_API_KEY
if not OPENAI_API_KEY:
    print("LỖI: Biến môi trường OPENAI_API_KEY chưa được thiết lập.")
    # Thoát hoặc xử lý lỗi phù hợp ở đây
else:
    try:
        genai.configure(api_key=OPENAI_API_KEY)
        print("Đã cấu hình OPENAI API thành công.")
    except Exception as e:
        print(f"LỖI CẤU HÌNH OPENAI API: {e}. Vui lòng kiểm tra API Key.")

OPENAI_MODEL_NAME = "gpt-4-0125-preview" # Hoặc model bạn muốn dùng
generation_config = {
    "max_output_tokens": 1500,
    "temperature": 0.7,
    "top_p": 1.0
}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# --- Cấu hình Google Drive API ---
DRIVE_SERVICE = None
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
# Lấy nội dung Service Account JSON từ biến môi trường
SERVICE_ACCOUNT_INFO_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
# Lấy Folder ID từ biến môi trường
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID')

if not SERVICE_ACCOUNT_INFO_JSON:
    print("LỖI: Biến môi trường GOOGLE_SERVICE_ACCOUNT_JSON chưa được thiết lập.")
if not DRIVE_FOLDER_ID:
     print("LỖI: Biến môi trường DRIVE_FOLDER_ID chưa được thiết lập.")

# --- Cấu hình File dữ liệu cán bộ ---
# Tên file vẫn giữ nguyên, file này cần được đưa lên repo GitHub cùng code
EMPLOYEE_DATA_FILE = 'can_bo.csv'

# === Biến toàn cục lưu trữ dữ liệu ===
WORD_FILES = {}
WORD_CONTENTS = {}

# === Các hàm hỗ trợ ===

def setup_drive_service():
    """Thiết lập kết nối đến Google Drive API sử dụng Service Account từ biến môi trường."""
    global DRIVE_SERVICE
    if not SERVICE_ACCOUNT_INFO_JSON:
         print("(!) Bỏ qua thiết lập Drive: Biến môi trường Service Account JSON chưa có.")
         DRIVE_SERVICE = None
         return

    try:
        # Parse chuỗi JSON từ biến môi trường thành dictionary
        service_account_info = json.loads(SERVICE_ACCOUNT_INFO_JSON)
        # Tạo thông tin xác thực từ dictionary
        creds = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES)
        # Xây dựng đối tượng dịch vụ Drive
        DRIVE_SERVICE = build('drive', 'v3', credentials=creds)
        print("-> Kết nối Google Drive API thành công.")
    except json.JSONDecodeError:
        print(f"LỖI: Không thể parse Service Account JSON từ biến môi trường. Google Drive sẽ không được sử dụng.")
        DRIVE_SERVICE = None
    except Exception as e:
        print(f"LỖI khi thiết lập kết nối Google Drive: {e}")
        DRIVE_SERVICE = None

# --- Các hàm get_all_word_files, load_word_file_contents giữ nguyên ---
def get_all_word_files(folder_id):
    """Lấy danh sách tên và ID các file Word (.docx) trong thư mục Google Drive chỉ định."""
    global DRIVE_SERVICE
    if not DRIVE_SERVICE:
        print("(!) Bỏ qua lấy file Word: Kết nối Google Drive chưa được thiết lập.")
        return {} # Trả về dict rỗng nếu không có kết nối
    if not folder_id:
         print("(!) Bỏ qua lấy file Word: DRIVE_FOLDER_ID chưa được thiết lập.")
         return {}

    files_found = {}
    page_token = None
    print(f"-> Bắt đầu tìm file Word trong thư mục Drive ID: {folder_id}...")
    try:
        while True:
            response = DRIVE_SERVICE.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document' and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            files = response.get('files', [])
            for file in files:
                print(f"  - Tìm thấy file: {file.get('name')} (ID: {file.get('id')})")
                files_found[file.get('name')] = file.get('id')
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
        if not files_found:
            print(f"-> Không tìm thấy file Word (.docx) nào trong thư mục {folder_id}.")
        else:
             print(f"-> Đã tìm thấy tổng cộng {len(files_found)} file Word.")
        return dict(sorted(files_found.items()))
    except HttpError as error:
        print(f'LỖI HttpError khi lấy danh sách file từ Drive: {error}')
        return {}
    except Exception as e:
        print(f'LỖI không xác định khi lấy danh sách file từ Drive: {e}')
        return {}

def load_word_file_contents(file_ids_dict):
    """Tải và đọc nội dung text từ các file Word dựa vào dictionary {tên file: id file}."""
    global DRIVE_SERVICE
    if not DRIVE_SERVICE or not file_ids_dict:
        print("(!) Bỏ qua tải nội dung file Word: Drive chưa kết nối hoặc không có file ID.")
        return {}

    contents = {}
    total_files = len(file_ids_dict)
    print(f"-> Bắt đầu tải và đọc nội dung cho {total_files} file Word...")
    count = 0
    for file_name, file_id in file_ids_dict.items():
        count += 1
        try:
            request_obj = DRIVE_SERVICE.files().get_media(fileId=file_id)
            file_content_bytes = request_obj.execute()
            with io.BytesIO(file_content_bytes) as f:
                document = Document(f)
                full_text = [para.text for para in document.paragraphs if para.text.strip()]
                contents[file_name] = '\n'.join(full_text)
            print(f"  - [{count}/{total_files}] Đã đọc xong: {file_name}")
        except HttpError as error:
            print(f'  - LỖI HttpError khi tải file {file_name} (ID: {file_id}): {error}')
        except Exception as e:
            print(f'  - LỖI {type(e).__name__} khi xử lý file {file_name} (ID: {file_id}): {e}')
    print(f"-> Hoàn tất đọc nội dung. Đã đọc thành công {len(contents)}/{total_files} file.")
    return contents

# === Các Route của Flask ===

@app.route('/')
def index():
    """Route chính, trả về trang giao diện chat (index.html)."""
    print("[Route] GET / - Hiển thị trang chat.")
    # Đảm bảo file index.html nằm trong thư mục 'templates' cùng cấp với app.py
    return render_template('index.html')

# --- Route /verify_employee giữ nguyên ---
@app.route('/verify_employee', methods=['POST'])
def verify_employee():
    """API Endpoint để xác thực mã cán bộ từ file CSV."""
    print("[Route] POST /verify_employee - Nhận yêu cầu xác thực mã cán bộ.")
    data = request.get_json()
    if not data or 'employee_id' not in data:
         print("(!) Lỗi yêu cầu: Thiếu employee_id trong JSON.")
         return jsonify({'status': 'error', 'message': 'Yêu cầu không hợp lệ, thiếu employee_id.'}), 400

    employee_id = data['employee_id'].strip()
    if not employee_id:
        print("(!) Lỗi yêu cầu: employee_id rỗng.")
        return jsonify({'status': 'error', 'message': 'Mã cán bộ không được để trống.'}), 400

    print(f"  - Đang tìm kiếm mã cán bộ: '{employee_id}'")
    # Kiểm tra xem file dữ liệu cán bộ có tồn tại không
    if not os.path.exists(EMPLOYEE_DATA_FILE):
         print(f"LỖI NGHIÊM TRỌNG: Không tìm thấy file dữ liệu '{EMPLOYEE_DATA_FILE}'.")
         return jsonify({'status': 'error', 'message': 'Lỗi hệ thống, không thể xác thực. Vui lòng liên hệ quản trị viên.'}), 500

    try:
        with open(EMPLOYEE_DATA_FILE, mode='r', newline='', encoding='utf-8-sig') as csvfile:
            reader = csv.DictReader(csvfile)
            required_columns = ['ma_can_bo', 'ho_ten', 'chuc_vu']
            if not all(col in reader.fieldnames for col in required_columns):
                print(f"LỖI: File CSV '{EMPLOYEE_DATA_FILE}' thiếu các cột cần thiết ({', '.join(required_columns)}).")
                return jsonify({'status': 'error', 'message': 'Lỗi cấu trúc file dữ liệu. Vui lòng liên hệ quản trị viên.'}), 500

            for row in reader:
                if row.get('ma_can_bo', '').strip() == employee_id:
                    ho_ten = row.get('ho_ten', 'N/A').strip()
                    chuc_vu = row.get('chuc_vu', '').strip()
                    greeting = f"Chào {chuc_vu} - {ho_ten}. Mã cán bộ {employee_id} đã được xác nhận. Xin mời đặt câu hỏi!"
                    # WORD_FILES được load khi khởi động ứng dụng
                    file_list = list(WORD_FILES.keys())
                    print(f"  -> Xác nhận thành công: Mã {employee_id} -> {chuc_vu} {ho_ten}.")
                    print(f"  -> Gửi về {len(file_list)} tên file Word.")
                    return jsonify({
                        'status': 'success',
                        'greeting': greeting,
                        'file_list': file_list
                    })

            print(f"  -> Xác nhận thất bại: Mã '{employee_id}' không tồn tại trong file.")
            return jsonify({'status': 'error', 'message': 'Mã cán bộ không tồn tại hoặc không đúng. Vui lòng thử lại.'})
    except FileNotFoundError:
         print(f"LỖI FileNotFoundError: Không thể mở file '{EMPLOYEE_DATA_FILE}' dù đã kiểm tra tồn tại.")
         return jsonify({'status': 'error', 'message': 'Lỗi hệ thống, không thể đọc dữ liệu. Vui lòng liên hệ quản trị viên.'}), 500
    except Exception as e:
        print(f"LỖI không xác định khi xử lý file CSV hoặc xác thực: {e}")
        return jsonify({'status': 'error', 'message': 'Đã xảy ra lỗi trong quá trình xác thực. Vui lòng thử lại sau.'}), 500

# --- Route /ask giữ nguyên logic prompt ---
@app.route('/ask', methods=['POST'])
def ask():
    """API Endpoint để nhận câu hỏi và trả lời bằng Gemini dựa trên nội dung file Word."""
    print("[Route] POST /ask - Nhận câu hỏi từ người dùng.")
    data = request.get_json()
    if not data or 'question' not in data:
         print("(!) Lỗi yêu cầu: Thiếu 'question' trong JSON.")
         return jsonify({'error': 'Yêu cầu không hợp lệ, thiếu câu hỏi.'}), 400
    question = data['question'].strip()
    if not question:
        print("(!) Lỗi yêu cầu: Câu hỏi rỗng.")
        return jsonify({'error': 'Câu hỏi không được để trống.'}), 400
    print(f"  - Câu hỏi nhận được: \"{question}\"")

    prompt = ""
    if not WORD_CONTENTS:
        print("  (!) Không có nội dung file Word. Trả lời dựa trên kiến thức chung.")
        prompt = f"Trả lời câu hỏi sau một cách ngắn gọn và chính xác: {question}"
    else:
        print(f"  - Sử dụng nội dung từ {len(WORD_CONTENTS)} file Word làm ngữ cảnh.")
        all_context = "\n\n---\n\n".join(WORD_CONTENTS.values())
        file_names_str = ", ".join(WORD_FILES.keys())
        prompt = f"""
Bạn là một trợ lý AI thông minh chuyên trả lời các câu hỏi dựa trên tài liệu nội bộ.

Dưới đây là nội dung tổng hợp từ các văn bản ({file_names_str}):

<<<
{all_context}
>>>

Nhiệm vụ: Dựa **chỉ** vào thông tin trong các văn bản trên, hãy trả lời câu hỏi sau một cách ngắn gọn, chính xác và đầy đủ:

Câu hỏi: "{question}"

Nếu không thể tìm thấy câu trả lời trong văn bản, hãy trả lời: "Tôi không tìm thấy thông tin này trong tài liệu."
"""
    print("  - Đang gửi yêu cầu đến OpenAI API...")
    if not OPENAI_API_KEY: # Kiểm tra lại API key trước khi gọi
         return jsonify({'error': 'Lỗi cấu hình phía server: Không tìm thấy OpenAI API Key.'}), 500
    try:
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL_NAME,
            messages=[
                {"role": "system", "content": "Bạn là một trợ lý AI chuyên trả lời câu hỏi từ tài liệu nội bộ. Chỉ trả lời dựa vào nội dung được cung cấp."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=1500,
        )
        answer = response['choices'][0]['message']['content']
        return jsonify({'answer': answer})
    except Exception as e:
        print(f"LỖI khi gọi OpenAI API: {e}")
        return jsonify({'error': f'Đã xảy ra lỗi khi giao tiếp với AI. Vui lòng thử lại sau.'}), 500

# === Khối thực thi chính khi chạy file app.py (chỉ chạy khi start bằng python app.py) ===
# Phần này sẽ không được Render sử dụng trực tiếp, nhưng hữu ích để kiểm tra cấu hình ban đầu
if __name__ != '__main__': # Thay đổi điều kiện để code bên dưới chạy khi import
    print("="*30)
    print("KHỞI TẠO ỨNG DỤNG TD.BHD AI CHAT ")
    print("="*30)
    print("\n[Bước 1/3] Thiết lập Google Drive...")
    setup_drive_service()
    print("\n[Bước 2/3] Tải dữ liệu văn bản từ Google Drive...")
    if DRIVE_SERVICE and DRIVE_FOLDER_ID:
        WORD_FILES = get_all_word_files(DRIVE_FOLDER_ID)
        if WORD_FILES:
            WORD_CONTENTS = load_word_file_contents(WORD_FILES)
        else:
            WORD_FILES = {}
            WORD_CONTENTS = {}
    else:
        print("-> Bỏ qua tải file Word do không có kết nối Google Drive hoặc thiếu Folder ID.")
        WORD_FILES = {}
        WORD_CONTENTS = {}
    print(f"\n[Bước 3/3] Kiểm tra file dữ liệu cán bộ ('{EMPLOYEE_DATA_FILE}')...")
    if not os.path.exists(EMPLOYEE_DATA_FILE):
        print(f"CẢNH BÁO: File '{EMPLOYEE_DATA_FILE}' không tìm thấy!")
        print(" -> Chức năng xác thực mã cán bộ sẽ KHÔNG hoạt động.")
    else:
        print(f"-> File '{EMPLOYEE_DATA_FILE}' đã sẵn sàng.")
    print("\n" + "="*30)
    print("KHỞI TẠO HOÀN TẤT!")
    print(f"- Trạng thái Google Drive: {'Đã kết nối' if DRIVE_SERVICE else 'Không kết nối / Lỗi cấu hình'}")
    print(f"- Số file Word được tải: {len(WORD_FILES)}")
    print(f"- File dữ liệu cán bộ: {'Sẵn sàng' if os.path.exists(EMPLOYEE_DATA_FILE) else 'Không tìm thấy'}")
    print("="*30)

# Lưu ý: KHÔNG CÓ app.run() ở đây nữa. Render sẽ dùng Gunicorn.