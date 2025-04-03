# PDF AI Assistant

An intelligent PDF processing application that allows users to upload, search, and chat with your PDF documents using Cohere's AI capabilities. The application supports large PDF files (up to 300MB) and provides semantic search and natural language interactions with document content.

## Features

- 📁 Large PDF file support (up to 300MB) with chunked uploading
- 🔍 Semantic search across PDF content
- 💬 Natural language chat interface with document context
- 🖼️ Image extraction and analysis
- ⚡ Real-time processing with progress tracking
- 📱 Responsive UI design
- 🔄 Rate-limited API calls for stable performance

## Technology Stack

- **Backend**: Python, Flask
- **Frontend**: JavaScript, HTML5, Tailwind CSS
- **AI/ML**: Cohere Models via AWS Bedrock (embeddings and chat)
- **PDF Processing**: PyMuPDF (fitz), PyPDF2
- **Dependencies**: See requirements.txt

## Installation

1. Clone the repository:
```bash
git clone https://github.com/aws-samples/Cohere-on-AWS
# Navigate to this demo's directory containing the README.md you're reading
```

2. Create and activate a virtual environment:
```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

3. Install required packages:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root and update it with your API Key:
```env
FLASK_ENV=development
FLASK_APP=app.py
```

5. Create a file at ~/.aws/credentials and update it with your AWS credentials
```
[default]
aws_access_key_id = <<your_access_key>>
aws_secret_access_key = <<your_secret_key>>>>
```

## Project Structure

```
pdf-ai-assistant/
├── static/
│   └── js/
│       └── script.js
├── templates/
│   └── index.html
├── uploads/           # Created automatically
├── temp_chunks/       # Created automatically
├── app.py            # Flask application
├── pdf_processor_cohere.py  # PDF processing logic
├── requirements.txt
└── README.md
```

## Usage

1. Start the Flask application:
```bash
python app.py
```

This Python script is designed to process PDF documents, extract text and images, compute embeddings for both, and perform search and chat functionalities using Cohere's models via AWS Bedrock.

**Workflow**:
- PDF Processing
   - Open the PDF and process each page.
   - Extract text, chunk it, and compute embeddings.
   - Extract images, convert them to base64, and compute embeddings.
- Search and Rerank
   - Compute embeddings for a query and find similar content using dot product similarity.
   - Optionally, rerank text results using Cohere's rerank model.
- Chat Query
   - Combine search results with Cohere's command model to generate text based answers and include relevant images.

2. Open your browser and navigate to:
```
http://127.0.0.1:5000
```

3. Use the interface to:
   - Upload PDF files (drag & drop or click to upload)
   - Search through document content
   - Ask questions about the document
   - View extracted images and their relevance to queries



## Rate Limiting

The application includes built-in rate limiting for Cohere API calls:
- Image embedding: 40 requests/minute
- Text processing: Managed by chunk size

## Error Handling

The application includes comprehensive error handling for:
- File size limits
- API rate limits
- Processing errors
- Invalid file types