import asyncio
import os
import logging
from fastapi import FastAPI, File, UploadFile, WebSocket, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uvicorn
from typing import List
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads"
OUTPUT_DIR = "output"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Queue to handle image processing
image_queue = asyncio.Queue()
websocket_clients = set()

# Simulate processing time
async def simulate_ai_processing(websocket: WebSocket, filename: str):
    total_steps = 30
    for i in range(total_steps):
        await asyncio.sleep(1) 
        # calculating progress percentage
        progress = int((i + 1) / total_steps * 100) 
        try:
            await websocket.send_json({"type": "progress", "value": progress, "filename": filename})
            logger.info(f"Sent progress update for {filename}: {progress}%")
        except Exception as e:
            logger.error(f"Error sending progress update for {filename}: {str(e)}")
            raise


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
        await simulate_ai_processing(websocket, filename)
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
            logger.info(f"Processing file: {filename}")
            image_path = os.path.join(UPLOAD_DIR, filename)
            for ws in list(websocket_clients):
                try:
                    video_path = await process_image(image_path, ws, filename)
                    await ws.send_json({
                        "type": "complete",
                        "video_url": f"/video/{os.path.basename(video_path)}",
                        "filename": filename
                    })
                    logger.info(f"Completed processing {filename}")
                except Exception as e:
                    logger.error(f"Error processing {filename} for a client: {str(e)}")
                    try:
                        await ws.send_json({
                            "type": "error",
                            "message": str(e),
                            "filename": filename
                        })
                    except Exception:
                        logger.error(f"Failed to send error message to client for {filename}")
                        websocket_clients.discard(ws)
            image_queue.task_done()
        except asyncio.CancelledError:
            logger.info("Worker task cancelled")
            break
        except Exception as e:
            logger.exception(f"Unexpected error in worker: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start worker task
    worker_task = asyncio.create_task(worker())
    yield
    # Shutdown worker task
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.add(websocket)
    logger.info("New WebSocket connection established")
    try:
        while True:
            message = await websocket.receive_text()
            logger.info(f"Received message from client: {message}")
    except Exception as e:
        logger.exception(f"WebSocket error: {str(e)}")
    finally:
        websocket_clients.remove(websocket)
        logger.info("WebSocket connection closed")

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)