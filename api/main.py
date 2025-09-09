import os
import json
import logging
import time
import uuid
import asyncio
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Union
from redis import Redis
import redis.asyncio as redis

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LLaMA API Service",
    docs_url="/docs",
    redoc_url="/redoc", 
    openapi_url="/openapi.json",
    root_path="/api"
)
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_pass = os.getenv("REDIS_PASS", None)
redis_conn = Redis.from_url(redis_url, password=redis_pass)
redis_client = redis.from_url(redis_url, password=redis_pass, decode_responses=True)

# Authentication setup
security = HTTPBearer()

# You can set valid tokens via environment variables
# In production, you'd typically validate against a database or external service
VALID_TOKENS = set()
if os.getenv("API_TOKENS"):
    # Comma-separated list of valid tokens
    VALID_TOKENS = set(os.getenv("API_TOKENS").split(","))
    logger.info(f"Loaded {len(VALID_TOKENS)} API tokens from environment")
else:
    logger.warning("No API_TOKENS found in environment variables! API will reject all requests.")
    # No default tokens in production for security

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Verify the bearer token from the Authorization header.
    Raises HTTPException if token is invalid.
    """
    token = credentials.credentials
    
    if not VALID_TOKENS:
        logger.error("No valid tokens configured. Check API_TOKENS environment variable.")
        raise HTTPException(
            status_code=500,
            detail="Authentication not properly configured",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if token not in VALID_TOKENS:
        logger.warning(f"Invalid token attempt: {token[:10]}...")
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"Valid token authenticated: {token[:10]}...")
    return token  # Return the validated token

class Prompt(BaseModel):
    text: str

# Unity LLM request models
class ChatRequest(BaseModel):
    prompt: str
    id_slot: Optional[int] = -1
    temperature: Optional[float] = 0.2
    top_k: Optional[int] = 40
    top_p: Optional[float] = 0.9
    min_p: Optional[float] = 0.05
    n_predict: Optional[int] = -1
    n_keep: Optional[int] = -1
    stream: Optional[bool] = True
    stop: Optional[List[str]] = None
    typical_p: Optional[float] = 1.0
    repeat_penalty: Optional[float] = 1.1
    repeat_last_n: Optional[int] = 64
    penalize_nl: Optional[bool] = True
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    penalty_prompt: Optional[str] = None
    mirostat: Optional[int] = 0
    mirostat_tau: Optional[float] = 5.0
    mirostat_eta: Optional[float] = 0.1
    grammar: Optional[str] = None
    seed: Optional[int] = 0
    ignore_eos: Optional[bool] = False
    logit_bias: Optional[Dict[int, str]] = None
    n_probs: Optional[int] = 0
    cache_prompt: Optional[bool] = True

class TokenizeRequest(BaseModel):
    content: str

# CompletionRequest - same as ChatRequest but for completion endpoint
class CompletionRequest(BaseModel):
    prompt: str
    id_slot: Optional[int] = -1
    temperature: Optional[float] = 0.2
    top_k: Optional[int] = 40
    top_p: Optional[float] = 0.9
    min_p: Optional[float] = 0.05
    n_predict: Optional[int] = -1
    n_keep: Optional[int] = -1
    stream: Optional[bool] = True
    stop: Optional[List[str]] = None
    typical_p: Optional[float] = 1.0
    repeat_penalty: Optional[float] = 1.1
    repeat_last_n: Optional[int] = 64
    penalize_nl: Optional[bool] = True
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    penalty_prompt: Optional[str] = None
    mirostat: Optional[int] = 0
    mirostat_tau: Optional[float] = 5.0
    mirostat_eta: Optional[float] = 0.1
    grammar: Optional[str] = None
    seed: Optional[int] = 0
    ignore_eos: Optional[bool] = False
    logit_bias: Optional[Dict[int, str]] = None
    n_probs: Optional[int] = 0
    cache_prompt: Optional[bool] = True

class SlotRequest(BaseModel):
    id_slot: int
    filepath: str
    action: str

# Unity LLM endpoints
@app.post("/template")
async def get_template(token: str = Depends(verify_token)):
    """Get the chat template from the LLaMA server via Redis async tasks"""
    try:
        logger.info("Creating template task for async processing")
        
        # Create task for Redis
        task_data = {
            "endpoint": "template",
            "data": {},
            "timestamp": time.time()
        }
        
        # Add to Redis queue
        task_id = str(uuid.uuid4())
        await redis_client.lpush("gpu_tasks", json.dumps({
            "id": task_id,
            **task_data
        }))
        
        logger.info(f"Template task {task_id} added to Redis queue")
        
        # Wait for result
        result = await wait_for_result(task_id, timeout=30)
        if result.get("error"):
            logger.error(f"Template task error: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])
        
        logger.info(f"Template result: {result.get('data', {})}")
        return result.get("data", {})
            
    except Exception as e:
        logger.error(f"Error processing template request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing template request: {str(e)}")

@app.post("/tokenize")
async def tokenize(request: TokenizeRequest, token: str = Depends(verify_token)):
    """Tokenize text via Redis async tasks"""
    try:
        logger.info(f"Creating tokenize task: {request.content[:50]}...")
        
        # Create task for Redis
        task_data = {
            "endpoint": "tokenize",
            "data": request.dict(),
            "timestamp": time.time()
        }
        
        # Add to Redis queue
        task_id = str(uuid.uuid4())
        await redis_client.lpush("gpu_tasks", json.dumps({
            "id": task_id,
            **task_data
        }))
        
        logger.info(f"Tokenize task {task_id} added to Redis queue")
        
        # Wait for result
        result = await wait_for_result(task_id, timeout=30)
        if result.get("error"):
            logger.error(f"Tokenize task error: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])
        
        logger.info(f"Tokenize result: {result.get('data', {})}")
        return result.get("data", {})
            
    except Exception as e:
        logger.error(f"Error processing tokenize request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing tokenize request: {str(e)}")

async def wait_for_result(task_id: str, timeout: int = 300):
    """Wait for a result from Redis with timeout"""
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        result_key = f"result:{task_id}"
        result_data = await redis_client.get(result_key)
        
        if result_data:
            result = json.loads(result_data)
            await redis_client.delete(result_key)  # Clean up
            return result
        
        await asyncio.sleep(0.1)  # Check every 100ms
    
    return {"error": "Request timeout"}

async def stream_completion_response(task_id: str):
    """Stream completion response as it arrives from GPU worker"""
    logger.info(f"Starting stream for task {task_id}")
    
    timeout = 300  # 5 minutes timeout
    start_time = time.time()
    chunks_sent = False
    last_chunk_count = 0
    
    while time.time() - start_time < timeout:
        try:
            # Check for streaming updates first (priority for real-time streaming)
            stream_key = f"stream:{task_id}"
            # Get only new chunks since last check
            current_chunk_count = await redis_client.llen(stream_key)
            
            if current_chunk_count > last_chunk_count:
                # Get only the new chunks
                new_chunks = await redis_client.lrange(stream_key, last_chunk_count, current_chunk_count - 1)
                
                if new_chunks:
                    logger.info(f"Got {len(new_chunks)} new streaming chunks for task {task_id}")
                    # Process each new streaming chunk immediately
                    for chunk_data in new_chunks:
                        try:
                            chunk = json.loads(chunk_data)
                            chunk_json = json.dumps(chunk)
                            logger.info(f"Yielding stream chunk: data: {chunk_json}")
                            yield f"data: {chunk_json}\n\n"
                            chunks_sent = True
                            # Very small delay to ensure proper streaming
                            await asyncio.sleep(0.001)
                        except json.JSONDecodeError:
                            logger.error(f"Invalid JSON in stream chunk: {chunk_data}")
                    
                    last_chunk_count = current_chunk_count
            
            # Check for final result
            result_key = f"result:{task_id}"
            result_data = await redis_client.get(result_key)
            
            if result_data:
                result = json.loads(result_data)
                logger.info(f"Got final result for task {task_id}: streaming finished")
                
                if result.get("error"):
                    error_response = {"error": result["error"]}
                    error_json = json.dumps(error_response)
                    logger.info(f"Yielding error: data: {error_json}")
                    yield f"data: {error_json}\n\n"
                else:
                    # Send final stop signal if we sent chunks
                    response_data = result.get("data", {})
                    if chunks_sent and response_data.get("stop", False):
                        # Send final stop signal
                        final_chunk = {
                            "content": "",
                            "multimodal": response_data.get("multimodal", False),
                            "slot_id": response_data.get("slot_id", 0),
                            "stop": True
                        }
                        final_json = json.dumps(final_chunk)
                        logger.info(f"Yielding final stop chunk: data: {final_json}")
                        yield f"data: {final_json}\n\n"
                    elif not chunks_sent:
                        # No streaming chunks were sent, send the complete result
                        response_json = json.dumps(response_data)
                        logger.info(f"Yielding complete response: data: {response_json}")
                        yield f"data: {response_json}\n\n"
                
                # Clean up
                await redis_client.delete(result_key)
                await redis_client.delete(stream_key)
                return
            
            await asyncio.sleep(0.05)  # Check every 50ms for new tokens
            
        except Exception as e:
            logger.error(f"Error in stream for task {task_id}: {e}")
            error_response = {"error": f"Streaming error: {str(e)}"}
            error_json = json.dumps(error_response)
            logger.info(f"Yielding exception error: data: {error_json}")
            yield f"data: {error_json}\n\n"
            return
    
    # Timeout reached
    logger.error(f"Timeout reached for task {task_id}")
    timeout_response = {"error": "Request timeout"}
    timeout_json = json.dumps(timeout_response)
    logger.info(f"Yielding timeout error: data: {timeout_json}")
    yield f"data: {timeout_json}\n\n"

@app.post("/completion")
async def completion(request: CompletionRequest, token: str = Depends(verify_token)):
    """Handle completion requests with streaming support"""
    logger.info(f"Completion request received: {request.prompt[:50]}...")
    
    try:
        # Create task for Redis
        task_data = {
            "endpoint": "completion",
            "data": request.dict(),
            "timestamp": time.time()
        }
        
        # Add to Redis queue
        task_id = str(uuid.uuid4())
        await redis_client.lpush("gpu_tasks", json.dumps({
            "id": task_id,
            **task_data
        }))
        
        logger.info(f"Task {task_id} added to Redis queue")
        
        # Check if streaming is requested
        if request.stream:
            return StreamingResponse(
                stream_completion_response(task_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"  # Disable nginx buffering if behind nginx
                }
            )
        else:
            # Wait for result
            result = await wait_for_result(task_id)
            if result.get("error"):
                raise HTTPException(status_code=500, detail=result["error"])
            return result["data"]
            
    except Exception as e:
        logger.error(f"Error in completion: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/slots")
async def handle_slots(request: SlotRequest, token: str = Depends(verify_token)):
    """Handle slot operations (save/restore cache) via Redis async tasks"""
    try:
        logger.info(f"Creating slots task: {request.dict()}")
        
        # Create task for Redis
        task_data = {
            "endpoint": "slots",
            "data": request.dict(),
            "timestamp": time.time()
        }
        
        # Add to Redis queue
        task_id = str(uuid.uuid4())
        await redis_client.lpush("gpu_tasks", json.dumps({
            "id": task_id,
            **task_data
        }))
        
        logger.info(f"Slots task {task_id} added to Redis queue")
        
        # Wait for result
        result = await wait_for_result(task_id, timeout=30)
        if result.get("error"):
            logger.error(f"Slots task error: {result['error']}")
            raise HTTPException(status_code=500, detail=result["error"])
        
        logger.info(f"Slots result: {result.get('data', {})}")
        return result.get("data", {})
            
    except Exception as e:
        logger.error(f"Error in slots endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in slots: {str(e)}")

@app.get("/")
def root(token: str = Depends(verify_token)):
    return {
        "message": "LLM API Server", 
        "status": "running",
        "endpoints": [
            "/template", "/tokenize", "/completion", "/slots"
        ],
        "architecture": "Distributed Redis async task-based worker system",
        "authentication": "Authentication required for all endpoints except /health, use a Bearer token"
    }

@app.get("/health")
def health_check():
    """Health check endpoint that doesn't require authentication"""
    return {"status": "healthy", "timestamp": time.time()}


