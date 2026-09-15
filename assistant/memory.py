import logging
logger = logging.getLogger(__name__)

import os
import uuid
from pathlib import Path
from assistant.speech import speak
import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def get_data_dir() -> Path:
    override = os.environ.get("VAVE_DATA_DIR")
    if override:
        return Path(override)
    return PROJECT_ROOT / "data"

DATA_DIR = get_data_dir()

# Initialize ChromaDB Persistent Client
chroma_client = None
memory_collection = None
document_collection = None
_memory_enabled = False

def reset_chroma(data_dir=None):
    global chroma_client, memory_collection, document_collection, _memory_enabled, DATA_DIR
    if data_dir:
        DATA_DIR = Path(data_dir)
    else:
        DATA_DIR = get_data_dir()
    try:
        import chromadb
        chroma_client = chromadb.PersistentClient(path=str(DATA_DIR / "chroma_db"))
        memory_collection = chroma_client.get_or_create_collection(
            name="vave_memory",
            metadata={"hnsw:space": "cosine"}
        )
        document_collection = chroma_client.get_or_create_collection(
            name="vave_documents",
            metadata={"hnsw:space": "cosine"}
        )
        _memory_enabled = True
    except Exception as e:
        logger.info(f"[VAVE] WARNING: Could not initialize ChromaDB (Semantic Memory is disabled). Error: {e}")
        chroma_client = None
        memory_collection = None
        document_collection = None
        _memory_enabled = False

reset_chroma()

def remember_fact(fact):
    """
    Saves a fact into VAVE's permanent ChromaDB memory.
    """
    if not _memory_enabled:
        return False
        
    try:
        # Generate a unique ID for this fact
        fact_id = str(uuid.uuid4())
        
        # Add to the vector database
        memory_collection.add(
            documents=[fact],
            ids=[fact_id]
        )
        speak("I have committed that to my permanent vector memory.")
        return True
    except Exception as e:
        logger.info(f"[Memory Error] Could not save to ChromaDB: {e}")
        return False

def get_relevant_memories_text(query, n_results=3):
    """
    Searches the vector database for the top n_results most relevant facts to the query.
    """
    if not _memory_enabled:
        return ""
        
    try:
        if memory_collection.count() == 0:
            return "No permanent memories stored yet."
            
        results = memory_collection.query(
            query_texts=[query],
            n_results=min(n_results, memory_collection.count())
        )
        
        documents = results.get("documents", [[]])[0]
        
        if not documents:
            return "No relevant permanent memories found."
            
        return "\n".join([f"- {doc}" for doc in documents])
    except Exception as e:
        logger.info(f"[Memory Error] Failed to query ChromaDB: {e}")
        return "Memory retrieval failed."


def ingest_document(file_path):
    """Reads a PDF or text file, chunks it, and saves it to the document vector database."""
    if not _memory_enabled:
        return "Memory is disabled."
        
    try:
        if not os.path.exists(file_path):
            return f"File not found: {file_path}"
            
        text = ""
        if file_path.lower().endswith(".pdf"):
            import PyPDF2
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
                
        if not text.strip():
            return "Document is empty or could not be read."
            
        speak("Ingesting document. This might take a moment.")
        
        # Simple chunking strategy (e.g. 500 characters per chunk)
        chunk_size = 500
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
        
        # We delete old document chunks for this file if we are re-ingesting
        # Note: A real app might want to keep track of sources. We will just use the file name as metadata.
        filename = os.path.basename(file_path)
        
        ids = [f"{filename}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{"source": filename} for _ in chunks]
        
        document_collection.add(
            documents=chunks,
            ids=ids,
            metadatas=metadatas
        )
        
        speak("Document ingested successfully.")
        return f"Ingested {len(chunks)} chunks from {filename}."
    except Exception as e:
        speak("Failed to ingest document.")
        return f"Error ingesting document: {e}"

def query_document(question, n_results=5):
    """Searches the document database for relevant chunks."""
    if not _memory_enabled:
        return "Memory is disabled."
        
    try:
        if document_collection.count() == 0:
            return "No documents ingested yet."
            
        results = document_collection.query(
            query_texts=[question],
            n_results=n_results
        )
        
        docs = results.get("documents", [[]])[0]
        if not docs:
            return "No relevant information found in documents."
            
        context = "\n...\n".join(docs)
        return context
    except Exception as e:
        return f"Error querying documents: {e}"
