import random
import os
import uuid
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import yt_dlp
import shutil

app = FastAPI()

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
# app.mount("/static", StaticFiles(directory="static"), name="static")

# Model to handle request payload
class VideoRequest(BaseModel):
    url: str


# Random messages for logging progress
def random_progress_message(step: str) -> str:
    messages = {
        "download": [
            "Initializing video download...",
            "Fetching video, please wait...",
            "Downloading the video now...",
            "Video download in progress...",
            "Preparing video for download...",
            "Connecting to video server..."
        ],
        "video_analysis": [
            "Analyzing video metadata...",
            "Fetching video details...",
            "Analyzing video content...",
            "Extracting video data...",
            "Parsing video information...",
            "Looking for video metadata..."
        ],
        "chapter_extraction": [
            "Extracting chapters from the video...",
            "Identifying video chapters...",
            "Chapter extraction in progress...",
            "Preparing chapter split...",
            "Scanning video for chapters...",
            "Analyzing chapter markers..."
        ],
        "file_conversion": [
            "Converting video to MP3...",
            "Preparing audio conversion...",
            "Converting video into MP3 format...",
            "Video to MP3 conversion starting...",
            "Converting video to high-quality MP3...",
            "Starting conversion to MP3..."
        ],
    }
    
    return random.choice(messages.get(step, ["Processing..."]))



# Function to get video metadata
def get_video_info(url: str) -> Optional[Dict]:
    print(random_progress_message("video_analysis"))  # Log random progress message for analysis
    ydl_opts = {
        'quiet': True,
        'extract_flat': False,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"Error fetching video info: {e}")
        return None


def get_video_chapters(url: str) -> Optional[List[Dict]]: 
    """Extract just the chapters from video info"""
    info = get_video_info(url)
    if info and 'chapters' in info:
        return info['chapters']
    return None


def download_video(url: str, temp_dir: str) -> Optional[str]:
    """Download the video and return the file path"""
    print(random_progress_message("download"))  # Log random progress message for downloading
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(temp_dir, 'full_video.%(ext)s'),
        'quiet': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        print(f"Error downloading video: {e}")
        return None

def get_file_size(filepath: str) -> str:
    """Get file size in MB"""
    size_bytes = os.path.getsize(filepath)
    size_mb = size_bytes / (1024 * 1024)
    return f"{size_mb:.2f} MB"


def get_duration(start: float, end: float) -> str:
    """Get duration in HH:MM:SS format"""
    duration = end - start
    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    seconds = int(duration % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def split_video_by_chapters(input_path: str, chapters: List[Dict], output_dir: str) -> List[Dict]:
    """Split video into chapters and return metadata with size and duration"""
    print(random_progress_message("chapter_extraction"))  # Log random progress message for chapter extraction
    output_files = []
    for i, chapter in enumerate(chapters, 1):
        start_time = chapter['start_time']
        end_time = chapter.get('end_time')
        title = chapter.get('title', f'Chapter {i}')

        # Clean filename
        clean_title = "".join(c if c.isalnum() else "_" for c in title)
        output_path = os.path.join(output_dir, f"{i}_{clean_title}.mp4")

        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-ss', str(start_time),
            '-to', str(end_time) if end_time else None,
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            output_path
        ]
        cmd = [arg for arg in cmd if arg is not None]

        try:
            subprocess.run(cmd, check=True)
            size = get_file_size(output_path)
            duration = get_duration(start_time, end_time)
            output_files.append({
                "path": output_path,
                "size": size,
                "duration": duration
            })
        except subprocess.CalledProcessError as e:
            print(f"Error splitting chapter {i}: {e.stderr}")

    return output_files


# Function to handle event-streaming of progress messages
@app.get("/api/extract-progress/{url}")
async def extract_video_progress(url: str):
    def event_generator():
        yield f"data: {random_progress_message('download')}\n\n"
        time.sleep(2)

        yield f"data: {random_progress_message('video_analysis')}\n\n"
        time.sleep(2)

        yield f"data: {random_progress_message('chapter_extraction')}\n\n"
        time.sleep(2)

        yield f"data: {random_progress_message('file_conversion')}\n\n"
        time.sleep(2)

    return EventSourceResponse(event_generator())


@app.post("/api/extract")
async def api_extract_chapters(request: VideoRequest):  
    """Extract chapters with size and duration"""
    temp_dir = os.path.join("temp", str(uuid.uuid4()))
    os.makedirs(temp_dir, exist_ok=True)

    try:
        print(random_progress_message("chapter_extraction"))  # Log progress message
        chapters = get_video_chapters(request.url)
        if not chapters:
            raise HTTPException(status_code=400, detail="No chapters found in this video")

        video_info = get_video_info(request.url)
        if not video_info:
            raise HTTPException(status_code=500, detail="Failed to fetch video info")

        video_title = video_info.get('title', 'No Title Found')
        video_thumbnail = video_info.get('thumbnail', '')

        video_path = download_video(request.url, temp_dir)
        if not video_path:
            raise HTTPException(status_code=500, detail="Failed to download video")

        chapter_files = split_video_by_chapters(video_path, chapters, temp_dir)

        # Return the video title, thumbnail, and chapters with size and duration
        return {
            "title": video_title,
            "thumbnail": video_thumbnail,
            "chapters": [
                {
                    "title": chapter["title"],
                    "start_time": chapter["start_time"],
                    "end_time": chapter.get('end_time'),
                    "size": file["size"],
                    "duration": file["duration"],
                    "mp4_download_url": f"/api/download/{os.path.basename(temp_dir)}/{os.path.basename(file['path'])}",
                    "mp3_download_url": f"/api/download/{os.path.basename(temp_dir)}/{os.path.basename(file['path']).replace('.mp4', '.mp3')}"
                }
                for chapter, file in zip(chapters, chapter_files)
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download/{temp_dir}/{filename}")
async def download_chapter(temp_dir: str, filename: str):
    """Download a specific chapter"""
    file_path = os.path.join("temp", temp_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        media_type='video/mp4',
        filename=filename
    )


@app.get("/api/download/{temp_dir}/{filename}")
async def download_mp3_chapter(temp_dir: str, filename: str):
    """Download a specific chapter as MP3"""
    file_path = os.path.join("temp", temp_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Convert video to MP3
    print(random_progress_message("file_conversion"))  # Log message for file conversion
    mp3_filename = filename.replace(".mp4", ".mp3")
    mp3_path = file_path.replace(".mp4", ".mp3")

    # Use ffmpeg to convert the video to mp3
    try:
        subprocess.run(
            ["ffmpeg", "-i", file_path, "-vn", "-acodec", "libmp3lame", mp3_path],
            check=True
        )
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Error converting to MP3")

    return FileResponse(
        mp3_path,
        media_type='audio/mpeg',
        filename=mp3_filename
    )


@app.get("/api/download/thumbnail/{temp_dir}")
async def download_thumbnail(temp_dir: str):
    """Download the thumbnail image for the video"""
    # Path to the thumbnail (assuming it's downloaded during video info extraction)
    file_path = os.path.join("temp", temp_dir, "thumbnail.jpg")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    return FileResponse(
        file_path,
        media_type='image/jpeg',
        filename="thumbnail.jpg"
    )

def clean_temp_folder(temp_folder_path, max_files=10):
    # List all files in the folder
    files = [os.path.join(temp_folder_path, f) for f in os.listdir(temp_folder_path)]
    
    # Sort files by creation time (oldest first)
    files.sort(key=lambda x: os.path.getctime(x))
    
    # If there are more than `max_files`, delete the oldest ones
    while len(files) > max_files:
        oldest_file = files.pop(0)  # Get the oldest file
        os.remove(oldest_file)  # Delete it
        print(f"Deleted: {oldest_file}")
