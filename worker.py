import os
import uuid
import subprocess
import time
import shutil
from pathlib import Path
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    url: str

def get_video_info(url: str) -> Optional[Dict]:
    print("Fetching video metadata...")
    ydl_opts = {
        'quiet': True,
        'extract_flat': False,
        'no_warnings': True,
        'cookiefile': 'cookies.txt'
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"Error fetching video info: {e}")
        return None



def get_video_chapters(url: str) -> Optional[List[Dict]]: 
    print("Checking for video chapters...")
    info = get_video_info(url)
    if info and 'chapters' in info:
        return info['chapters']
    return None

def download_video(url: str, temp_dir: str) -> Optional[str]:
    print("Downloading full video...")
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(temp_dir, 'full_video.%(ext)s'),
        'quiet': True,
        'cookiefile': 'cookies.txt'
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        print(f"Error downloading video: {e}")
        return None


def get_file_size(filepath: str) -> str:
    size_bytes = os.path.getsize(filepath)
    size_mb = size_bytes / (1024 * 1024)
    return f"{size_mb:.2f} MB"

def get_duration(start: float, end: float) -> str:
    duration = end - start
    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    seconds = int(duration % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def split_video_by_chapters(input_path: str, chapters: List[Dict], output_dir: str) -> List[Dict]:
    print("Splitting video by chapters and converting to MP3...")
    output_files = []

    for i, chapter in enumerate(chapters, 1):
        start_time = chapter['start_time']
        end_time = chapter.get('end_time')
        title = chapter.get('title', f'Chapter {i}')
        clean_title = "".join(c if c.isalnum() else "_" for c in title)

        mp4_output_path = os.path.join(output_dir, f"{i}_{clean_title}.mp4")
        mp3_output_path = os.path.join(output_dir, f"{i}_{clean_title}.mp3")

        mp4_cmd = [
            'ffmpeg',
            '-i', input_path,
            '-ss', str(start_time),
            '-to', str(end_time) if end_time else None,
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            '-y',
            mp4_output_path
        ]
        mp4_cmd = [arg for arg in mp4_cmd if arg is not None]

        try:
            subprocess.run(mp4_cmd, check=True)

            mp3_cmd = [
                'ffmpeg',
                '-i', mp4_output_path,
                '-vn',
                '-acodec', 'libmp3lame',
                '-q:a', '2',
                '-y',
                mp3_output_path
            ]
            subprocess.run(mp3_cmd, check=True)

            size = get_file_size(mp4_output_path)
            duration = get_duration(start_time, end_time)

            output_files.append({
                "path": mp4_output_path,
                "mp3_path": mp3_output_path,
                "size": size,
                "duration": duration
            })

        except subprocess.CalledProcessError as e:
            print(f"Error processing chapter {i}: {e}")

    return output_files

@app.get("/api/extract-progress/{url}")
async def extract_video_progress(url: str):
    def event_generator():
        yield f"data: Downloading video...\n\n"
        time.sleep(2)
        yield f"data: Fetching video metadata...\n\n"
        time.sleep(2)
        yield f"data: Extracting chapters...\n\n"
        time.sleep(2)
        yield f"data: Converting chapters to MP3...\n\n"
        time.sleep(2)
    return EventSourceResponse(event_generator())

@app.post("/api/extract")
async def api_extract_chapters(request: VideoRequest):  
    temp_dir = os.path.join("temp", str(uuid.uuid4()))
    os.makedirs(temp_dir, exist_ok=True)

    try:
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

        return {
            "title": video_title,
            "thumbnail": video_thumbnail,
            "chapters": [
                {
                    "title": chapter["title"],
                    "start_time": chapter["start_time"],
                    "end_time": chapter.get("end_time"),
                    "size": file["size"],
                    "duration": file["duration"],
                    "mp4_download_url": f"/api/download/{os.path.basename(temp_dir)}/{os.path.basename(file['path'])}",
                    "mp3_download_url": f"/api/download/{os.path.basename(temp_dir)}/{os.path.basename(file['mp3_path'])}"
                }
                for chapter, file in zip(chapters, chapter_files)
            ]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{temp_dir}/{filename}")
async def download_chapter(temp_dir: str, filename: str):
    file_path = os.path.join("temp", temp_dir, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = 'audio/mp3' if filename.endswith(".mp3") else 'video/mp4'
    return FileResponse(file_path, media_type=media_type, filename=filename)

@app.get("/api/download/thumbnail/{temp_dir}")
async def download_thumbnail(temp_dir: str):
    file_path = os.path.join("temp", temp_dir, "thumbnail.jpg")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(file_path, media_type='image/jpeg', filename="thumbnail.jpg")

def clean_temp_folder(temp_root="temp", max_folders=10):
    if not os.path.exists(temp_root):
        return
    folders = [os.path.join(temp_root, d) for d in os.listdir(temp_root) if os.path.isdir(os.path.join(temp_root, d))]
    folders.sort(key=lambda x: os.path.getctime(x))
    while len(folders) > max_folders:
        oldest = folders.pop(0)
        shutil.rmtree(oldest)
        print(f"Deleted folder: {oldest}")
