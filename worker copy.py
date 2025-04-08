import os
import shutil
import uuid
import yt_dlp
import subprocess
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

class LinkRequest(BaseModel):
    link: str

def clean_temp_directory(max_dirs=10):
    folders = [f for f in os.listdir(TEMP_DIR) if os.path.isdir(os.path.join(TEMP_DIR, f))]
    folders.sort(key=lambda f: os.path.getctime(os.path.join(TEMP_DIR, f)))

    while len(folders) > max_dirs:
        oldest = folders.pop(0)
        shutil.rmtree(os.path.join(TEMP_DIR, oldest))

def get_chapters(video_info) -> List[Dict[str, Optional[str]]]:
    chapters = video_info.get("chapters", [])
    result = []
    for i, chapter in enumerate(chapters):
        start_time = chapter.get("start_time")
        end_time = chapters[i + 1]["start_time"] if i + 1 < len(chapters) else None
        title = chapter.get("title", f"Chapter {i + 1}")
        result.append({
            "title": title,
            "start_time": start_time,
            "end_time": end_time
        })
    return result

def get_file_size(path: str) -> float:
    return round(os.path.getsize(path) / (1024 * 1024), 2)

def get_duration(start: float, end: Optional[float]) -> str:
    if end is None:
        return "Unknown"
    duration = int(end - start)
    return str(timedelta(seconds=duration))

def split_video_by_chapters(input_path: str, chapters: List[Dict], output_dir: str) -> List[Dict]:
    output_files = []

    for i, chapter in enumerate(chapters, 1):
        start_time = chapter['start_time']
        end_time = chapter.get('end_time')
        title = chapter.get('title', f'Chapter {i}')
        clean_title = "".join(c if c.isalnum() else "_" for c in title)[:80]

        mp4_output_path = os.path.join(output_dir, f"{i}_{clean_title}.mp4")
        mp3_output_path = os.path.join(output_dir, f"{i}_{clean_title}.mp3")

        cmd_mp4 = [
            'ffmpeg', '-y', '-i', input_path,
            '-ss', str(start_time),
            '-c', 'copy', '-avoid_negative_ts', '1'
        ]

        if end_time:
            cmd_mp4.extend(['-to', str(end_time)])

        cmd_mp4.append(mp4_output_path)

        try:
            subprocess.run(cmd_mp4, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error splitting MP4 chapter {i}: {e}")
            continue

        try:
            subprocess.run(['ffmpeg', '-y', '-i', mp4_output_path, '-vn', '-acodec', 'libmp3lame', mp3_output_path], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error converting chapter {i} to MP3: {e}")

        output_files.append({
            "path": mp4_output_path,
            "size": get_file_size(mp4_output_path),
            "duration": get_duration(start_time, end_time),
            "mp4_download_url": f"/api/download/{os.path.basename(output_dir)}/{os.path.basename(mp4_output_path)}",
            "mp3_download_url": f"/api/download/{os.path.basename(output_dir)}/{os.path.basename(mp3_output_path)}"
        })

    return output_files

@app.post("/api/extract")
async def extract(request: LinkRequest):
    clean_temp_directory()

    link = request.link
    uid = str(uuid.uuid4())
    output_dir = os.path.join(TEMP_DIR, uid)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "full_video.%(ext)s")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'cookiefile': 'cookies.txt'
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = ydl.extract_info(link, download=True)
            downloaded_filename = ydl.prepare_filename(info_dict).replace(".webm", ".mp4").replace(".mkv", ".mp4")
            chapters = get_chapters(info_dict)
            
            if not chapters:
                raise HTTPException(status_code=400, detail="No chapters found in the video.")

            files = split_video_by_chapters(downloaded_filename, chapters, output_dir)

            thumbnail_url = info_dict.get("thumbnail")
            if thumbnail_url:
                thumbnail_path = os.path.join(output_dir, "thumbnail.jpg")
                subprocess.run(["curl", "-s", "-o", thumbnail_path, thumbnail_url], check=True)

            return JSONResponse({
                "videoId": uid,
                "title": info_dict.get("title", "Untitled"),
                "thumbnail": thumbnail_url or "",
                "chapters": files
            })
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{temp_dir}/{filename}")
async def download_file(temp_dir: str, filename: str):
    path = os.path.join(TEMP_DIR, temp_dir, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "video/mp4" if filename.endswith(".mp4") else "audio/mpeg"
    return FileResponse(path, media_type=media_type, filename=filename)

@app.get("/api/download/thumbnail/{temp_dir}")
async def download_thumbnail(temp_dir: str):
    path = os.path.join(TEMP_DIR, temp_dir, "thumbnail.jpg")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg", filename="thumbnail.jpg")
