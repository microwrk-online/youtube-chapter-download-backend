const express = require('express');
const cors = require('cors');
const { exec } = require('child_process');
const { v4: uuidv4 } = require('uuid');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = 5000;
const TEMP_DIR = path.join(__dirname, 'temp');

if (!fs.existsSync(TEMP_DIR)) fs.mkdirSync(TEMP_DIR);

app.use(cors());
app.use(express.json());

// Clean up older files beyond 10 folders
function cleanupOldFiles() {
  const folders = fs.readdirSync(TEMP_DIR)
    .map(name => ({
      name,
      time: fs.statSync(path.join(TEMP_DIR, name)).mtime.getTime()
    }))
    .sort((a, b) => b.time - a.time);

  for (let i = 10; i < folders.length; i++) {
    fs.rmSync(path.join(TEMP_DIR, folders[i].name), { recursive: true, force: true });
  }
}

// API route to extract video chapters
app.post('/api/extract', (req, res) => {
  const { url } = req.body;
  if (!url) return res.status(400).json({ error: 'No URL provided' });

  const id = uuidv4();
  const dir = path.join(TEMP_DIR, id);
  fs.mkdirSync(dir);

  const command = `yt-dlp --extract-audio --audio-format mp3 --write-info-json --write-thumbnail --split-chapters -o "${dir}/%(title)s [%(chapter_number)s] [%(chapter)s].%(ext)s" ${url}`;

  exec(command, (error, stdout, stderr) => {
    if (error) {
      console.error(error);
      return res.status(500).json({ error: 'Download failed', details: stderr });
    }

    // Get all files
    const files = fs.readdirSync(dir);
    const mp3s = files.filter(f => f.endsWith('.mp3'));
    const thumbnail = files.find(f => f.endsWith('.jpg') || f.endsWith('.webp'));
    const infoJson = files.find(f => f.endsWith('.info.json'));

    let title = 'Untitled';
    if (infoJson) {
      const info = JSON.parse(fs.readFileSync(path.join(dir, infoJson)));
      title = info.title || title;
    }

    const response = {
      id,
      title,
      thumbnail: thumbnail ? `http://localhost:${PORT}/temp/${id}/${thumbnail}` : null,
      chapters: mp3s.map(name => ({
        name,
        url: `http://localhost:${PORT}/temp/${id}/${name}`
      }))
    };

    cleanupOldFiles();
    res.json(response);
  });
});

// Static files route for MP3s and thumbnails
app.use('/temp', express.static(TEMP_DIR));

// Start the server
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
