from flask import Flask, request, render_template, jsonify, url_for, send_from_directory
from werkzeug.utils import secure_filename
import os
from pathlib import Path
from pdf_processor_cohere import PDFProcessorCohere
from dotenv import load_dotenv
import logging
import shutil
import tempfile
import time
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__,
            static_folder='static',
            static_url_path='/static')

# Configuration
app.config.update(
    UPLOAD_FOLDER='uploads',
    TEMP_FOLDER='temp_chunks',
    CHUNK_SIZE=5 * 1024 * 1024,  # 5MB chunks
    MAX_CONTENT_LENGTH=300 * 1024 * 1024,  # 300MB max file size
    ALLOWED_EXTENSIONS={'pdf'},
    SESSION_TIMEOUT=3600  # 1 hour
)

# Ensure all required directories exist
for folder in [app.config['UPLOAD_FOLDER'], app.config['TEMP_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# Initialize global variables
processor = None
processing_lock = False

def allowed_file(filename):
    """Check if the file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def cleanup_old_files():
    """Clean up old temporary files"""
    try:
        current_time = time.time()
        # Clean up temp folder
        for item in os.listdir(app.config['TEMP_FOLDER']):
            item_path = os.path.join(app.config['TEMP_FOLDER'], item)
            if os.path.getctime(item_path) < (current_time - app.config['SESSION_TIMEOUT']):
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
        
        # Clean up upload folder
        for item in os.listdir(app.config['UPLOAD_FOLDER']):
            item_path = os.path.join(app.config['UPLOAD_FOLDER'], item)
            if os.path.getctime(item_path) < (current_time - app.config['SESSION_TIMEOUT']):
                os.remove(item_path)
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")

@app.before_request
def before_request():
    """Perform cleanup before each request"""
    cleanup_old_files()

@app.route('/', methods=['GET'])
def index():
    """Render the main page"""
    return render_template('index.html')

@app.route('/upload-chunk', methods=['POST'])
def upload_chunk():
    """Handle chunk upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        chunk_number = int(request.form['chunk'])
        total_chunks = int(request.form['totalChunks'])
        filename = secure_filename(request.form['filename'])
        
        if not file or not filename:
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(filename):
            return jsonify({'error': 'File type not allowed'}), 400
        
        # Create directory for this file's chunks
        chunk_dir = Path(app.config['TEMP_FOLDER']) / f"{filename}_chunks"
        chunk_dir.mkdir(exist_ok=True)
        
        # Save the chunk
        chunk_path = chunk_dir / f"chunk_{chunk_number}"
        file.save(chunk_path)
        
        logger.info(f"Chunk {chunk_number + 1}/{total_chunks} uploaded for {filename}")
        
        return jsonify({
            'message': f'Chunk {chunk_number + 1}/{total_chunks} uploaded successfully',
            'progress': ((chunk_number + 1) / total_chunks) * 100
        })
        
    except Exception as e:
        logger.error(f"Error uploading chunk: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/finalize-upload', methods=['POST'])
def finalize_upload():
    """Finalize the upload by merging chunks and processing the PDF"""
    global processor, processing_lock
    
    if processing_lock:
        return jsonify({'error': 'Another file is currently being processed'}), 429
    
    processing_lock = True
    
    try:
        data = request.json
        filename = secure_filename(data['filename'])
        chunk_dir = Path(app.config['TEMP_FOLDER']) / f"{filename}_chunks"
        
        if not chunk_dir.exists():
            return jsonify({'error': 'No chunks found'}), 400
        
        # Merge chunks
        output_path = Path(app.config['UPLOAD_FOLDER']) / filename
        with output_path.open('wb') as output_file:
            chunk_paths = sorted(chunk_dir.glob('chunk_*'), 
                               key=lambda x: int(x.name.split('_')[1]))
            
            for chunk_path in chunk_paths:
                with chunk_path.open('rb') as chunk_file:
                    output_file.write(chunk_file.read())
        
        # Clean up chunks
        shutil.rmtree(chunk_dir)
        
        processor = PDFProcessorCohere()
        
        # Process the PDF
        content_sequence = processor.process_pdf(str(output_path))
        
        # Clean up the merged file
        output_path.unlink()
        
        # Remove embeddings from response
        response_sequence = []
        for item in content_sequence:
            item_copy = item.copy()
            item_copy.pop('embedding', None)
            response_sequence.append(item_copy)
        
        logger.info(f"Successfully processed {filename}")
        
        return jsonify({
            'content_sequence': response_sequence,
            'message': 'File processed successfully'
        })
        
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
    finally:
        processing_lock = False

@app.route('/search', methods=['POST'])
def search():
    """Search through processed content using both embedding and rerank"""
    global processor
    
    if not processor:
        return jsonify({'error': 'No document processed yet'}), 400
    
    try:
        data = request.json
        query = data.get('query')
        use_rerank = data.get('use_rerank', False)
        
        if not query:
            return jsonify({'error': 'No query provided'}), 400
        
        # Get embedding results
        embed_results = processor.search(query)
        
        # Get rerank results only if toggle is on
        rerank_results = processor.rerank_search(query) if use_rerank else None
        
        return jsonify({
            'embed_results': embed_results,
            'rerank_results': rerank_results,
            'message': 'Search completed successfully'
        })
        
    except Exception as e:
        logger.error(f"Error during search: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    """Handle chat queries about the document"""
    global processor
    
    if not processor:
        return jsonify({'error': 'No document processed yet'}), 400
    
    try:
        data = request.json
        query = data.get('query')
        
        if not query:
            return jsonify({'error': 'No query provided'}), 400
        
        # Get chat response
        response = processor.chat_query(query)
        
        # Format the response properly
        formatted_response = {
            'answer': response.get('answer', 'No answer available'),
            'images': response.get('images', []),
            'sources': response.get('sources', [])
        }
        
        logger.info(f"Chat query completed for: {query}")
        return jsonify(formatted_response)
        
    except Exception as e:
        logger.error(f"Error during chat query: {str(e)}")
        return jsonify({
            'error': str(e),
            'message': 'Error processing chat query'
        }), 500

@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file size too large error"""
    return jsonify({
        'error': 'File too large',
        'message': f'Maximum file size is {app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)}MB'
    }), 413

@app.errorhandler(500)
def internal_server_error(error):
    """Handle internal server errors"""
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({
        'error': 'Internal server error',
        'message': 'An unexpected error occurred'
    }), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Handle all other exceptions"""
    logger.error(f"Unhandled exception: {str(e)}")
    return jsonify({
        'error': 'Server error',
        'message': str(e)
    }), 500

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    # Set up logging to file
    file_handler = logging.FileHandler('app.log')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('PDF Processor startup')
    
    # Run the application
    app.run(debug=True)
