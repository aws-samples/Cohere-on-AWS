from PyPDF2 import PdfReader
from PIL import Image
import io
import base64
import fitz
import cohere
import numpy as np
from typing import List, Dict, Any, Optional
import os
import time
from datetime import datetime, timedelta
import logging
import cohere_aws
import json
import boto3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, max_requests: int, time_window: int):
        """
        Initialize rate limiter
        max_requests: Maximum number of requests allowed in the time window
        time_window: Time window in seconds
        """
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
        
    def wait_if_needed(self):
        """Wait if rate limit is exceeded"""
        now = datetime.now()
        
        # Remove old requests outside the time window
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < timedelta(seconds=self.time_window)]
        
        if len(self.requests) >= self.max_requests:
            # Calculate how long to wait
            oldest_request = self.requests[0]
            wait_time = (oldest_request + timedelta(seconds=self.time_window) - now).total_seconds()
            if wait_time > 0:
                logger.info(f"Rate limit reached. Waiting {wait_time:.2f} seconds...")
                time.sleep(wait_time)
            self.requests = self.requests[1:]
        
        self.requests.append(now)

class PDFProcessorCohere:
    def __init__(self, chunk_size: int = 1000):
        self.content_sequence = []
        self.embeddings = []
        self.chunk_size = chunk_size
        # Initialize rate limiter for 40 requests per minute
        self.rate_limiter = RateLimiter(max_requests=40, time_window=60)

    def chunk_text(self, text: str) -> List[str]:
        """Split text into chunks while preserving sentence boundaries"""
        sentences = text.replace('\n', ' ').split('. ')
        chunks = []
        current_chunk = []
        current_length = 0
        
        for sentence in sentences:
            sentence = sentence.strip() + '. '
            sentence_length = len(sentence)
            
            if current_length + sentence_length > self.chunk_size:
                if current_chunk:
                    chunks.append(''.join(current_chunk))
                current_chunk = [sentence]
                current_length = sentence_length
            else:
                current_chunk.append(sentence)
                current_length += sentence_length
        
        if current_chunk:
            chunks.append(''.join(current_chunk))
            
        return chunks

    def compute_embeddings(self, content_item: Dict[str, Any]) -> Optional[dict]:
        """Compute embeddings for a single content item"""
        
        model_id = "cohere.embed-english-v3"
        bedrock_runtime = boto3.client(
           service_name="bedrock-runtime",
           region_name="us-west-2" # Replace with your AWS region
        )
        try:
            if content_item['type'] == 'text':
                body = json.dumps({
                  "texts": [content_item['content']],              
                  "input_type": "search_document"
                })
                response = bedrock_runtime.invoke_model(
                  body=body,
                  modelId=model_id,
                  contentType="application/json"
                )
                response_body = json.loads(response.get("body").read())
                embedding_values = response_body.get("embeddings")[0]

                return {
                    'embedding': embedding_values,
                    'type': 'text'
                }
            else:  # image
                # Apply rate limiting for image embeddings
                self.rate_limiter.wait_if_needed()
                
                image_uri = f"data:image/{content_item['format']};base64,{content_item['base64_data']}"
                body = json.dumps({
                  "images": [image_uri],
                  "input_type": "image"
                })
                response = bedrock_runtime.invoke_model(
                  body=body,
                  modelId=model_id,
                  accept="application/json",
                  contentType="application/json"
                )

                response_body = json.loads(response.get("body").read())
                embedding_values = response_body.get("embeddings")[0]
                return {
                    'embedding': embedding_values,
                    'type': 'image'
                }
        except Exception as e:
            logger.error(f"Error computing embedding for {content_item['type']}: {str(e)}")
            return None

    def process_pdf(self, pdf_path: str) -> List[Dict[str, Any]]:
        """Process PDF with chunked text processing and image extraction"""
        doc = fitz.open(pdf_path)
        logger.info(f"Processing PDF: {pdf_path}")
        
        try:
            total_pages = len(doc)
            chunk_id = 0
            for page_num in range(total_pages):
                page = doc[page_num]
                logger.info(f"Processing page {page_num + 1}/{total_pages}")
                
                # Get text blocks with their coordinates
                text = page.get_text()
                if text.strip():
                    chunks = self.chunk_text(text)
                    for chunk_idx, chunk in enumerate(chunks):
                        if chunk.strip():
                            content_item = {
                                'page': page_num + 1,
                                'chunk': chunk_idx + 1,
                                'content': chunk.strip(),
                                'type': 'text',
                                'bbox': tuple(page.bound()),
                                'chunk_id': chunk_id
                            }
                            chunk_id += 1
                            
                            embedding_data = self.compute_embeddings(content_item)
                            if embedding_data:
                                content_item['embedding'] = embedding_data['embedding']
                                self.content_sequence.append(content_item)
                
                # Enhanced image and vector graphic processing
                try:
                    # Extract images using get_images()
                    images = page.get_images(full=True)
                    for img_index, img in enumerate(images):
                        xref = img[0]  # Get the image reference
                        base_image = doc.extract_image(xref)
                        
                        if base_image and base_image["image"]:
                            image_bytes = base_image["image"]
                            image_format = base_image["ext"].lower()
                            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                            
                            image_item = {
                                'page': page_num + 1,
                                'index': img_index,
                                'type': 'image',
                                'format': image_format,
                                'base64_data': image_base64,
                                'chunk_id': chunk_id
                            }
                            chunk_id += 1
                            
                            embedding_data = self.compute_embeddings(image_item)
                            if embedding_data:
                                image_item['embedding'] = embedding_data['embedding']
                                self.content_sequence.append(image_item)

                    # Render vector graphics to images
                    pix = page.get_pixmap()
                    img_bytes = pix.tobytes()
                    
                    if img_bytes:
                        image_base64 = base64.b64encode(img_bytes).decode('utf-8')
                        image_item = {
                            'page': page_num + 1,
                            'index': 0,
                            'type': 'image',
                            'format': 'png',
                            'base64_data': image_base64,
                            'chunk_id': chunk_id
                        }
                        chunk_id += 1
                        
                        embedding_data = self.compute_embeddings(image_item)
                        if embedding_data:
                            image_item['embedding'] = embedding_data['embedding']
                            self.content_sequence.append(image_item)
                
                except Exception as e:
                    logger.error(f"Error processing images on page {page_num + 1}: {str(e)}")
            
        finally:
            doc.close()
            
        logger.info(f"Completed processing PDF with {len(self.content_sequence)} items")
        return self.content_sequence

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search through embedded content using a text query"""
        model_id = "cohere.embed-english-v3"
        bedrock_runtime = boto3.client(
           service_name="bedrock-runtime",
           region_name="us-west-2" # Replace with your AWS region
        )

        try:
            body = json.dumps({
                  "texts": [query],
                  "input_type": "search_query"
            })
            query_response = bedrock_runtime.invoke_model(
                  body=body,
                  modelId=model_id,
                  contentType="application/json"
            )
            response_body = json.loads(query_response.get("body").read())
            query_embedding = response_body.get("embeddings")[0]

            similarities = []
            for idx, item in enumerate(self.content_sequence):
                if 'embedding' in item:
                    similarity = np.dot(query_embedding, item['embedding'])
                    similarities.append((similarity, idx))

            similarities.sort(reverse=True)
            top_results = []
            for similarity, idx in similarities[:top_k]:
                result = self.content_sequence[idx].copy()
                result.pop('embedding', None)
                result['similarity_score'] = float(similarity)
                top_results.append(result)

            return top_results
        except Exception as e:
            logger.error(f"Error during search: {str(e)}")
            return []

    def rerank_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search through text content using Cohere's rerank model"""
        try:
            # Prepare documents for reranking - text only
            documents = []
            doc_mapping = []  # To maintain reference to original items
            
            logger.info(f"Total items in content_sequence: {len(self.content_sequence)}")
            
            for idx, item in enumerate(self.content_sequence):
                if item['type'] == 'text':  # Only process text items
                    documents.append(item['content'])
                    doc_mapping.append(idx)
            
            logger.info(f"Number of text documents collected: {len(documents)}")
            
            if not documents:
                logger.warning("No text documents found for reranking")
                return []
            
            # Log the query
            logger.info(f"Query: {query}")
            
            # Perform reranking
            bedrock_agent_runtime = boto3.client('bedrock-agent-runtime',region_name='us-west-2')
            rerank_modelId = "cohere.rerank-v3-5:0"
            rerank_package_arn = f"arn:aws:bedrock:us-west-2::foundation-model/{rerank_modelId}"

            text_sources = []
            for text in documents:
             text_sources.append({
              "type": "INLINE",
              "inlineDocumentSource": {
                "type": "TEXT",
                "textDocument": {
                  "text": text,
                }
              }
            })
            response = bedrock_agent_runtime.rerank(
             queries=[{
                "type": "TEXT",
                "textQuery": {
                    "text": query
                }
             }],
             sources=text_sources,
             rerankingConfiguration={
              "type": "BEDROCK_RERANKING_MODEL",
              "bedrockRerankingConfiguration": {
                "numberOfResults": min(top_k, len(documents)),
                "modelConfiguration": {
                  "modelArn": rerank_package_arn,
                }
               }
              }
            ) 
            logger.info(f"Rerank response received with {len(response['results'])} results")
            
            # Format results
            results = []
            for result in response['results']:
                idx = doc_mapping[result['index']]
                item = self.content_sequence[idx].copy()
                item.pop('embedding', None)
                item['relevance_score'] = float(result['relevanceScore'])
                results.append(item)
            
            logger.info(f"Formatted {len(results)} results for return")
            
            return results
            
        except Exception as e:
            logger.error(f"Error during rerank search: {str(e)}")
            logger.exception("Full traceback:")
            return []

    def chat_query(self, query: str, context_results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Enhanced chat query that returns answer with relevant images"""
        try:
            # Use rerank results for context if not provided
            if context_results is None:
                search_results = self.search_with_toggle(query, use_rerank=True)
                context_results = search_results['rerank_results']
                embed_results = search_results['embed_results']
            else:
                embed_results = self.search(query)

            text_context = []
            images = []

            for item in context_results:
                if item['type'] == 'text':
                    text_context.append(f"Content from page {item['page']}: {item['content']}")

            for item in embed_results:
                if item['type'] == 'image':
                    images.append({
                        'page': item['page'],
                        'format': item['format'],
                        'base64_data': item['base64_data'],
                        'similarity_score': item.get('similarity_score', 0)
                    })

            context = "\n\n".join(text_context)
            message = f"Based on the following context, please answer this question: {query}\n\nContext:\n{context}"

            ## Using Cohere's AWS SDK
            co = cohere_aws.Client(mode=cohere_aws.Mode.BEDROCK)
            response = co.chat(message=message, model_id="cohere.command-r-plus-v1:0", stream=False)

            # Process the response
            answer_text = "No response generated"
            if response:
                content = response.text
                if isinstance(content, list):
                    answer_text = ' '.join(item.text for item in content if hasattr(item, 'text'))
                else:
                    answer_text = str(content)

            result = {
                'answer': answer_text,
                'images': images,
                'sources': [
                    {
                        'page': item['page'],
                        'type': item['type'],
                        'content': item.get('content', '[Image]') if item['type'] == 'text' else '[Image]',
                        'similarity_score': item.get('similarity_score', 0)
                    }
                    for item in context_results
                ]
            }

            logger.info(f"Chat query completed successfully")
            return result

        except Exception as e:
            logger.error(f"Error during chat query: {str(e)}")
            return {
                'answer': f"Error processing query: {str(e)}",
                'images': [],
                'sources': []
            }

    def save_results(self, output_dir: str):
        """Save processed content and embeddings"""
        try:
            os.makedirs(output_dir, exist_ok=True)
            sequence_for_save = []
            for item in self.content_sequence:
                item_copy = item.copy()
                item_copy.pop('embedding', None)
                sequence_for_save.append(item_copy)
                
            with open(os.path.join(output_dir, 'content_sequence.json'), 'w') as f:
                import json
                json.dump(sequence_for_save, f, indent=2)
                
            logger.info(f"Results saved successfully to {output_dir}")
                
        except Exception as e:
            logger.error(f"Error saving results: {str(e)}")

    def search_with_toggle(self, query: str, use_rerank: bool = False, top_k: int = 5) -> Dict[str, Any]:
        """
        Search through content with toggle for rerank
        Args:
            query: Search query
            use_rerank: Toggle for using rerank (True to show both embed and rerank results)
            top_k: Number of top results to return
        """
        try:
            results = {
                'embed_results': self.search(query, top_k),
                'rerank_results': None,  # Default to None when rerank is not used
                'search_type': 'embedding' if not use_rerank else 'both'
            }

            # If rerank is toggled on, include rerank results
            if use_rerank:
                results['rerank_results'] = self.rerank_search(query, top_k)
            
            # Add summary stats
            results['stats'] = {
                'embed_count': len(results['embed_results']),
                'rerank_count': len(results['rerank_results']) if results['rerank_results'] else 0
            }
            
            logger.info(f"Search completed. Mode: {'both' if use_rerank else 'embedding only'}")
            logger.info(f"Found {results['stats']['embed_count']} embedding results" + 
                       (f" and {results['stats']['rerank_count']} rerank results" if use_rerank else ""))
            
            return results

        except Exception as e:
            logger.error(f"Error in search_with_toggle: {str(e)}")
            return {
                'embed_results': [],
                'rerank_results': None,
                'search_type': 'error',
                'error': str(e)
            }

    def process_content(self, content_sequence: List[Dict[str, Any]]) -> None:
        """Process the content sequence and create embeddings"""
        try:
            # Add chunk IDs to content items
            for idx, item in enumerate(content_sequence):
                item['chunk_id'] = idx
            
            self.content_sequence = content_sequence
            # Rest of the processing code remains the same
        except Exception as e:
            logger.error(f"Error processing content: {str(e)}")
