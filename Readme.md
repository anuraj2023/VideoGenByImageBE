# Video Generation from Image (AI model simulation)

## Overview

This FastAPI-based application provides a service for converting uploaded images into short video clips. It simulates AI processing and uses FFmpeg for video generation. The app features real-time progress updates via WebSocket connections and queue based processing to handle multiple requests efficiently.

## Features

- Image upload endpoint
- Asynchronous video generation
- WebSocket for real-time progress updates
- Health check endpoint
- CORS support for cross-origin requests
- Error handling and logging

## Prerequisites

- Python 3.7+
- FFmpeg installed and accessible in the system PATH

## Quick run

Make sure you have docker installed and running in your machine. <br/>

1. Clone the repository:
   ```
   git clone git@github.com:anuraj2023/VideoGenByImageBE.git
   cd VideoGenByImageBE
   ```

2. Run ```docker-compose up```

3. The API will be available at `http://localhost:8000`

## Installation (Detailed manner)

1. Clone the repository:
   ```
   git clone git@github.com:anuraj2023/VideoGenByImageBE.git
   cd VideoGenByImageBE
   ```

2. Create and activate a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate
   ```

3. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

## Running the Application

Start the server with:

```
uvicorn app:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

## API Endpoints

1. `GET /health`
   - Health check endpoint

2. `POST /upload`
   - Upload one or more image files
   - Returns a list of uploaded filenames

3. `WebSocket /ws`
   - Establishes a WebSocket connection for real-time progress updates

4. `GET /video/{video_name}`
   - Retrieve a generated video file

## Usage

1. Upload images using the `/upload` endpoint.
2. Connect to the WebSocket at `/ws` to receive progress updates.
3. Once processing is complete, retrieve the video using the `/video/{video_name}` endpoint.


