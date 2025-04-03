document.addEventListener('DOMContentLoaded', function() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const progressBar = document.getElementById('progressBar');
    const progressContainer = document.getElementById('progressContainer');
    const searchSection = document.getElementById('searchSection');
    const results = document.getElementById('results');
    const loading = document.getElementById('loading');
    
    let currentFile = null;
    let chunkSize = 1024 * 1024 * 5; // 5MB chunks
    let currentChunk = 0;
    let totalChunks = 0;

    // Drag and drop handlers
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('border-blue-500');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('border-blue-500');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-blue-500');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelection(files[0]);
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileSelection(e.target.files[0]);
        }
    });

    function handleFileSelection(file) {
        if (file.type !== 'application/pdf') {
            showError('Please select a PDF file');
            return;
        }

        if (file.size > 300 * 1024 * 1024) { // 300MB limit
            showError('File size exceeds 300MB limit');
            return;
        }

        currentFile = file;
        totalChunks = Math.ceil(file.size / chunkSize);
        currentChunk = 0;
        
        showProgress();
        uploadNextChunk();
    }

    function uploadNextChunk() {
        const start = currentChunk * chunkSize;
        const end = Math.min(start + chunkSize, currentFile.size);
        const chunk = currentFile.slice(start, end);
        const formData = new FormData();
        
        formData.append('file', chunk);
        formData.append('chunk', currentChunk);
        formData.append('totalChunks', totalChunks);
        formData.append('filename', currentFile.name);

        fetch('/upload-chunk', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            
            updateProgress((currentChunk + 1) / totalChunks * 100);
            
            if (currentChunk < totalChunks - 1) {
                currentChunk++;
                uploadNextChunk();
            } else {
                finalizeMerge();
            }
        })
        .catch(error => {
            showError('Upload failed: ' + error.message);
        });
    }

    function finalizeMerge() {
        fetch('/finalize-upload', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filename: currentFile.name
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            showSearchInterface(data);
        })
        .catch(error => {
            showError('Processing failed: ' + error.message);
        });
    }

    // UI Helper Functions
    function updateProgress(percentage) {
        progressBar.style.width = `${percentage}%`;
    }

    function showProgress() {
        progressContainer.classList.remove('hidden');
        loading.classList.remove('hidden');
        results.classList.add('hidden');
        searchSection.classList.add('hidden');
    }

    function showSearchInterface(data) {
        progressContainer.classList.add('hidden');
        loading.classList.add('hidden');
        searchSection.classList.remove('hidden');
        results.classList.remove('hidden');
        
        // Initialize search functionality
        initializeSearch();
        // Display initial content
        displayContentSequence(data.content_sequence);
    }

    function showError(message) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mt-4';
        errorDiv.textContent = message;
        
        const closeButton = document.createElement('button');
        closeButton.className = 'absolute top-0 right-0 px-4 py-3';
        closeButton.innerHTML = '×';
        closeButton.onclick = () => errorDiv.remove();
        
        errorDiv.appendChild(closeButton);
        document.querySelector('main').insertBefore(errorDiv, progressContainer);
        
        setTimeout(() => errorDiv.remove(), 5000);
    }

    function initializeSearch() {
        const searchForm = document.getElementById('searchForm');
        const chatForm = document.getElementById('chatForm');

        searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const query = document.getElementById('searchQuery').value;
            performSearch(query);
        });

        chatForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const query = document.getElementById('chatQuery').value.trim();
            if (!query) {
                alert('Please enter a question');
                return;
            }
            
            performChatQuery(query);
        });
    }

    // Search and Chat Query Functions
    function performSearch(query) {
        loading.classList.remove('hidden');
        const useRerank = document.getElementById('rerankToggle').checked;
        
        fetch('/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                query,
                use_rerank: useRerank 
            })
        })
        .then(response => response.json())
        .then(data => {
            displaySearchResults(data);
        })
        .catch(error => {
            showError('Search failed: ' + error.message);
        })
        .finally(() => {
            loading.classList.add('hidden');
        });
    }

    function performChatQuery(query) {
        const loading = document.getElementById('loading');
        loading.classList.remove('hidden');
        
        fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ query })
        })
        .then(response => response.json())
        .then(data => {
            displayChatResponse(data);
        })
        .catch(error => {
            showError('Chat query failed: ' + error.message);
        })
        .finally(() => {
            loading.classList.add('hidden');
        });
    }

    // Display Functions
    function displayContentSequence(sequence) {
        const container = document.getElementById('contentSequence');
        container.innerHTML = '';
        
        sequence.forEach((item, index) => {
            const itemDiv = document.createElement('div');
            itemDiv.className = 'p-4 border rounded-lg';
            
            if (item.type === 'text') {
                itemDiv.innerHTML = `
                    <p class="text-sm text-gray-500">Page ${item.page}</p>
                    <p class="mt-2">${item.content}</p>
                `;
            } else if (item.type === 'image') {
                itemDiv.innerHTML = `
                    <p class="text-sm text-gray-500">Page ${item.page} - Image</p>
                    <img src="data:image/${item.format};base64,${item.base64_data}" 
                         class="mt-2 max-w-full h-auto" 
                         alt="Page ${item.page} Image ${item.index}">
                `;
            }
            
            container.appendChild(itemDiv);
        });
    }

    function displaySearchResults(results) {
        const inlineContainer = document.getElementById('inlineSearchResultsContent');
        inlineContainer.innerHTML = '';
        document.getElementById('inlineSearchResults').classList.remove('hidden');
        
        const embedResults = results.embed_results || [];
        const rerankResults = results.rerank_results || [];
        const useRerank = document.getElementById('rerankToggle').checked;
        
        if (embedResults.length === 0 && rerankResults.length === 0) {
            inlineContainer.innerHTML = '<p class="text-gray-500 p-4">No results found</p>';
            return;
        }

        const containerHTML = `
            <div class="grid grid-cols-1 ${useRerank ? 'md:grid-cols-2' : ''} gap-4">
                <!-- Embedding Results Table -->
                <div class="overflow-x-auto">
                    <h4 class="text-lg font-semibold mb-2 text-blue-600">Embedding Results</h4>
                    <table class="min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Chunk ID</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Page</th>
                                <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Content</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200">
                            ${embedResults.map(result => `
                                <tr class="hover:bg-gray-50">
                                    <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">${result.chunk_id}</td>
                                    <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">${result.page}</td>
                                    <td class="px-4 py-4 text-sm text-gray-900">${result.type === 'text' ? result.content : 
                                        `<img src="data:image/${result.format};base64,${result.base64_data}" class="max-h-32 w-auto">`}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>

                ${useRerank ? `
                    <!-- Rerank Results Table -->
                    <div class="overflow-x-auto">
                        <h4 class="text-lg font-semibold mb-2 text-green-600">Rerank Results</h4>
                        <table class="min-w-full divide-y divide-gray-200">
                            <thead class="bg-gray-50">
                                <tr>
                                    <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Chunk ID</th>
                                    <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Page</th>
                                    <th scope="col" class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Content</th>
                                </tr>
                            </thead>
                            <tbody class="bg-white divide-y divide-gray-200">
                                ${rerankResults.map(result => `
                                    <tr class="hover:bg-gray-50">
                                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">${result.chunk_id}</td>
                                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">${result.page}</td>
                                        <td class="px-4 py-4 text-sm text-gray-900">${result.content}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                ` : ''}
            </div>
        `;
        
        inlineContainer.innerHTML = containerHTML;
    }

    function displayChatResponse(response) {
        const chatResponseContainer = document.getElementById('inlineChatResponseContent');
        if (!chatResponseContainer) {
            console.error('Chat response container not found');
            return; // Prevent further execution if the container is not found
        }
        chatResponseContainer.innerHTML = '';
        document.getElementById('inlineChatResponse').classList.remove('hidden');

        // Create response HTML with images before sources
        const responseHTML = `
            <div class="space-y-4">
                <!-- Answer -->
                <div class="prose max-w-none">
                    <p>${response.answer}</p>
                </div>

                <!-- Related Images (if any) -->
                ${response.images && response.images.length > 0 ? `
                    <div class="mt-4">
                        <h4 class="text-lg font-semibold mb-2 text-blue-600">Related Images</h4>
                        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                            ${response.images.map(img => `
                                <div class="border rounded-lg p-2">
                                    <img src="data:image/${img.format};base64,${img.base64_data}" 
                                         class="max-h-48 w-auto mx-auto"
                                         alt="Related image from page ${img.page}">
                                    <p class="text-sm text-gray-500 mt-1 text-center">Page ${img.page}</p>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}

                <!-- Sources -->
                ${response.sources && response.sources.length > 0 ? `
                    <div class="mt-4">
                        <h4 class="text-lg font-semibold mb-2 text-green-600">Sources</h4>
                        <div class="space-y-2">
                            ${response.sources.map(source => `
                                <div class="bg-gray-50 p-3 rounded-lg">
                                    <p class="text-sm text-gray-700">${source.content}</p>
                                    <p class="text-xs text-gray-500 mt-1">Page ${source.page}</p>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}
            </div>
        `;

        chatResponseContainer.innerHTML = responseHTML;
    }
});