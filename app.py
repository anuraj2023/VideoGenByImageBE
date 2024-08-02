import asyncio
import os
import logging
from fastapi import FastAPI, File, UploadFile, WebSocket, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
from typing import List
from contextlib import asynccontextmanager
from starlette.websockets import WebSocketDisconnect

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "output"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Queue to handle multiple image processing
image_queue = asyncio.Queue()
websocket_clients = set()
processed_images = set()

current_client = None

async def generate_video(image_path: str, output_path: str):
    ffmpeg_command = [
        "ffmpeg",
        "-y",                    
        "-loglevel", "verbose",  
        "-loop", "1",            
        "-i", image_path,        
        "-c:v", "libx264",       
        "-t", "5",               
        "-pix_fmt", "yuv420p",   
        "-vf", "scale=1280:720", 
        output_path              
    ]
    
    process = await asyncio.create_subprocess_exec(
        *ffmpeg_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    _, stderr = await process.communicate()
    
    if process.returncode != 0:
        logger.error(f"FFmpeg error: {stderr.decode()}")
        raise Exception(f"Video generation failed: {stderr.decode()}")
    
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise Exception(f"Generated video file is empty or does not exist: {output_path}")
    
    logger.info(f"Video generated successfully: {output_path}")

async def process_image(image_path: str, websocket: WebSocket, filename: str):
    output_path = os.path.join(OUTPUT_DIR, f"{os.path.splitext(os.path.basename(image_path))[0]}.mp4")
    
    try:
        total_steps = 11
        for i in range(10):
            await asyncio.sleep(3)
            progress = int((i + 1) / total_steps * 100)
            await websocket.send_json({"type": "progress", "value": progress, "filename": filename})
        
        await generate_video(image_path, output_path)
        
        # Final progress update after video generation
        await websocket.send_json({"type": "progress", "value": 100, "filename": filename})
        return output_path
    except WebSocketDisconnect:
        logger.warning(f"WebSocket disconnected during processing of {filename}")
        # Continue video processing even after websocket error
        await generate_video(image_path, output_path)
        return output_path
    except Exception as e:
        logger.exception(f"Error in process_image: {str(e)}")
        raise

async def worker():
    logger.info("Worker started")
    while True:
        try:
            filename = await image_queue.get()
            if filename not in processed_images:
                logger.info(f"Processing file: {filename}")
                image_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    # Using a copy of websocket_clients to avoid RuntimeError
                    clients = websocket_clients.copy()
                    if clients:
                        websocket = next(iter(clients))
                        video_path = await process_image(image_path, websocket, filename)
                        processed_images.add(filename)
                        for ws in clients:
                            try:
                                await ws.send_json({
                                    "type": "complete",
                                    "video_url": f"/video/{os.path.basename(video_path)}",
                                    "filename": filename
                                })
                            except Exception as e:
                                logger.error(f"Failed to send completion message to a client: {str(e)}")
                        logger.info(f"Completed processing {filename}")
                    else:
                        logger.warning("No WebSocket clients connected. Skipping processing.")
                except Exception as e:
                    logger.error(f"Error processing {filename}: {str(e)}")
                    for ws in websocket_clients.copy():
                        try:
                            await ws.send_json({
                                "type": "error",
                                "message": str(e),
                                "filename": filename
                            })
                        except Exception:
                            logger.error(f"Failed to send error message to client for {filename}")
            else:
                logger.info(f"Skipping already processed file: {filename}")
            image_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Worker task cancelled")
            break
        except Exception as e:
            logger.exception(f"Unexpected error in worker: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(worker())
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        logger.info("Worker task cancelled")

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    filenames = []
    for file in files:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        try:
            with open(file_path, "wb") as buffer:
                buffer.write(await file.read())
            filenames.append(file.filename)
            logger.info(f"File uploaded successfully: {file_path}")
            await image_queue.put(file.filename)
            logger.info(f"Added {file.filename} to processing queue")
        except Exception as e:
            logger.exception(f"Error uploading file: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    return {"files": filenames}

async def keepalive():
    while True:
        for websocket in websocket_clients:
            try:
                await websocket.send_json({"type": "keepalive"})
            except Exception:
                pass
        await asyncio.sleep(30)

@app.get("/video/{video_name}")
async def get_video(video_name: str, range: str = Header(None)):
    video_path = os.path.join(OUTPUT_DIR, video_name)
    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        raise HTTPException(status_code=404, detail="Video not found")
    
    headers = {
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
    }
    
    return FileResponse(
        video_path,
        media_type="video/mp4",
        headers=headers,
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global current_client
    
    logger.info(f"New WebSocket connection attempt: {id(websocket)}")
    
    try:
        await websocket.accept()
        logger.info(f"WebSocket connection accepted: {id(websocket)}")
        
        try:
            # Wait for the initial message from the client with a timeout
            initial_message = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            logger.info(f"Received initial message: {initial_message}")
            
            if initial_message.get('type') != 'init':
                logger.warning(f"Unexpected initial message: {initial_message}")
                await websocket.send_json({"type": "error", "message": "Invalid initial message"})
                await websocket.close(code=1003, reason="Invalid initial message")
                return
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for initial message")
            await websocket.send_json({"type": "error", "message": "Timeout waiting for initial message"})
            await websocket.close(code=1001, reason="Timeout waiting for initial message")
            return
        except Exception as e:
            logger.exception(f"Error receiving initial message: {str(e)}")
            await websocket.send_json({"type": "error", "message": "Error receiving initial message"})
            await websocket.close(code=1011, reason="Error receiving initial message")
            return
        
        if current_client is not None:
            logger.info(f"Rejecting connection, current client exists: {id(current_client)}")
            await websocket.send_json({"type": "error", "message": "Another client is already connected"})
            await websocket.close(code=1000, reason="Another client is already connected")
            return

        current_client = websocket
        websocket_clients.add(websocket)
        
        # Send an initial message to confirm connection
        await websocket.send_json({"type": "connection", "status": "established"})
        
        logger.info(f"WebSocket connection fully established: {id(websocket)}")

        # Keep the connection alive
        while True:
            try:
                message = await websocket.receive_json()
                logger.info(f"Received message from client {id(websocket)}: {message}")
                if message.get('type') == 'ping':
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {id(websocket)}")
                break
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                await websocket.send_json({"type": "error", "message": f"Error processing message: {str(e)}"})
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during connection setup: {id(websocket)}")
    except Exception as e:
        logger.exception(f"Unexpected error in WebSocket connection: {str(e)}")
    finally:
        websocket_clients.discard(websocket)
        if current_client == websocket:
            current_client = None
        logger.info(f"WebSocket connection closed and removed from clients: {id(websocket)}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keepalive())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)