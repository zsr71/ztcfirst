from flask import Flask, request, render_template, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
import os
import subprocess
import logging
import tempfile
import uuid
import signal
import sys

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static/slides'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['STATIC_FOLDER'] = STATIC_FOLDER

ALLOWED_EXTENSIONS = {'pptx', 'pdf'}

# 配置日志
logger = logging.getLogger()
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)
app.logger.handlers = []
app.logger.propagate = False
app.logger.addHandler(file_handler)
app.logger.addHandler(stream_handler)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_unique_filename(original_filename):
    unique_id = str(uuid.uuid4())
    extension = original_filename.rsplit('.', 1)[1].lower()
    return f"{unique_id}.{extension}"

def get_commentary_filename(unique_id):
    return f"commentary_{unique_id}.txt"

def convert_ppt_to_images(ppt_path, output_folder):
    session_id = str(uuid.uuid4())
    session_output_folder = os.path.join(output_folder, session_id)
    os.makedirs(session_output_folder, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            ppt_path = os.path.abspath(ppt_path)
            app.logger.info(f"PPT path: {ppt_path}")

            soffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
            if not os.path.exists(soffice_path):
                app.logger.error(f"soffice.exe not found at {soffice_path}")
                return []

            ppt_filename = os.path.splitext(os.path.basename(ppt_path))[0]
            pdf_filename = f"{ppt_filename}.pdf"
            pdf_path = os.path.join(temp_dir, pdf_filename)
            command = [soffice_path, '--headless', '--convert-to', 'pdf', ppt_path, '--outdir', temp_dir]
            app.logger.info(f"Running command to convert PPT to PDF: {command}")
            result = subprocess.run(command, check=True, timeout=60, capture_output=True, text=True)
            app.logger.info(f"LibreOffice output: {result.stdout}")

            if not os.path.exists(pdf_path):
                app.logger.error(f"PDF file not generated at {pdf_path}")
                return []

            images = convert_from_path(pdf_path)
            slide_images = []
            for i, image in enumerate(images):
                new_filename = f"slide_{i+1}.png"
                dest_path = os.path.join(session_output_folder, new_filename)
                image.save(dest_path, 'PNG')
                slide_images.append(os.path.join(session_id, new_filename))
                app.logger.info(f"Saved slide {i+1} to {dest_path}")

            app.logger.info(f"Converted {ppt_path} to {len(slide_images)} images")
            return slide_images
        except Exception as e:
            app.logger.error(f"Error converting PPT: {str(e)}")
            return []

def convert_pdf_to_images(pdf_path, output_folder):
    session_id = str(uuid.uuid4())
    session_output_folder = os.path.join(output_folder, session_id)
    os.makedirs(session_output_folder, exist_ok=True)
    
    try:
        images = convert_from_path(pdf_path)
        slide_images = []
        for i, image in enumerate(images):
            new_filename = f"slide_{i+1}.png"
            dest_path = os.path.join(session_output_folder, new_filename)
            image.save(dest_path, 'PNG')
            slide_images.append(os.path.join(session_id, new_filename))
            app.logger.info(f"Saved PDF page {i+1} to {dest_path}")
        
        app.logger.info(f"Converted {pdf_path} to {len(slide_images)} images")
        return slide_images
    except Exception as e:
        app.logger.error(f"Error converting PDF: {str(e)}")
        return []

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part"
        file = request.files['file']
        if file.filename == '':
            return "No selected file"
        if not allowed_file(file.filename):
            return "File type not allowed. Please upload a .pptx or .pdf file"

        unique_filename = generate_unique_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)

        file_extension = unique_filename.rsplit('.', 1)[1].lower()
        if file_extension == 'pptx':
            slide_images = convert_ppt_to_images(file_path, app.config['STATIC_FOLDER'])
        elif file_extension == 'pdf':
            slide_images = convert_pdf_to_images(file_path, app.config['STATIC_FOLDER'])
        
        if not slide_images:
            return "Failed to process file. Check logs for details."
        
        return render_template('index.html', 
                            slides=slide_images, 
                            total_slides=len(slide_images),
                            unique_id=unique_filename.split('.')[0])

    return render_template('index.html', slides=None)

@app.route('/static/slides/<path:filename>')
def serve_slide(filename):
    return send_from_directory(app.config['STATIC_FOLDER'], filename)

@app.route('/save_commentary', methods=['POST'])
def save_commentary():
    data = request.get_json()
    slide_number = data.get('slide_number')
    commentary = data.get('commentary')
    unique_id = data.get('unique_id')
    
    if not slide_number or not commentary or not unique_id:
        return jsonify({"error": "Slide number, commentary or unique_id is missing"}), 400
    
    commentary_file = get_commentary_filename(unique_id)
    commentary_dict = {}
    
    if os.path.exists(commentary_file):
        try:
            with open(commentary_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if '-' in line:
                        page, text = line.split('-', 1)
                        commentary_dict[int(page.strip())] = text.strip()
        except Exception as e:
            app.logger.error(f"Error reading commentary file: {str(e)}")
    
    commentary_dict[int(slide_number)] = commentary.strip()
    
    try:
        with open(commentary_file, 'w', encoding='utf-8') as f:
            for page in sorted(commentary_dict.keys()):
                f.write(f"{page} - {commentary_dict[page]}\n")
        return jsonify({"message": "Commentary saved successfully"})
    except Exception as e:
        app.logger.error(f"Error writing commentary file: {str(e)}")
        return jsonify({"error": "Failed to save commentary"}), 500

@app.route('/get_commentary/<unique_id>/<int:slide_number>', methods=['GET'])
def get_commentary(unique_id, slide_number):
    commentary_file = get_commentary_filename(unique_id)
    commentary_dict = {}
    
    if os.path.exists(commentary_file):
        try:
            with open(commentary_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if '-' in line:
                        page, text = line.split('-', 1)
                        commentary_dict[int(page.strip())] = text.strip()
        except Exception as e:
            app.logger.error(f"Error reading commentary file: {str(e)}")
            return jsonify({"error": "Failed to read commentary file"}), 500
    
    if slide_number in commentary_dict:
        return jsonify({"commentary": commentary_dict[slide_number]})
    else:
        return jsonify({"commentary": None})

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    if 'audio' not in request.files:
        return jsonify({"error": "No audio file"}), 400
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    audio_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'audio')
    os.makedirs(audio_dir, exist_ok=True)
    
    # 使用原始文件名（如 slide_1_recording.wav），避免重复生成唯一 ID
    filename = secure_filename(audio_file.filename)
    save_path = os.path.join(audio_dir, filename)
    
    try:
        audio_file.save(save_path)
        app.logger.info(f"录音文件保存至: {save_path}")
        return jsonify({"message": "录音保存成功", "filename": filename}), 200
    except Exception as e:
        app.logger.error(f"保存录音失败: {str(e)}")
        return jsonify({"error": "服务器错误"}), 500

if __name__ == '__main__':
    def signal_handler(sig, frame):
        print('Shutting down server...')
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    if not os.path.exists(STATIC_FOLDER):
        os.makedirs(STATIC_FOLDER, exist_ok=True)
    try:
        app.run(host='0.0.0.0', port=8080, debug=True)
    except KeyboardInterrupt:
        print("Server stopped by user")
        sys.exit(0)