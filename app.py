from flask import Flask, request, jsonify, send_file, render_template
import yt_dlp
import os
import subprocess
import json
import re
import time

app = Flask(__name__)

# Define the temporary directory for downloads
# Ensure this directory exists or create it
TEMP_DIR = 'temp_downloads'
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Route for the main page (index.html)
@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')

# Route for the "About Us" page
@app.route('/about')
@app.route('/about.html')
def about():
    return render_template('about.html')

# Route for the "Contact Us" page
@app.route('/contact')
@app.route('/contact.html')
def contact():
    return render_template('contact.html')

# Route for the "Q&A (FAQ)" page
@app.route('/faq')
@app.route('/faq.html')
def faq():
    return render_template('faq.html')

# Route to fetch video information and filtered formats
@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({"error": "URL is required"}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'dump_single_json': True,
            'no_warnings': True,
            'listformats': False
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        available_formats = {} # To store the best format for each quality (video+audio or video-only)
        best_audio_format = None

        # Target qualities in descending order
        target_heights = [2160, 1440, 1080, 720, 480, 360, 144] # 4K, 2K, 1080p, 720p, 480p, 360p, 144p

        for f in info.get('formats', []):
            format_id = f.get('format_id')
            ext = f.get('ext')
            height = f.get('height')
            vcodec = f.get('vcodec')
            acodec = f.get('acodec')
            filesize = f.get('filesize') or f.get('filesize_approx')

            if filesize is None: # Ignore formats with no known file size
                continue
            
            # Find the best audio format
            if acodec and acodec != 'none' and (not vcodec or vcodec == 'none'):
                # Prefer common or high-quality audio formats
                if ext in ['m4a', 'opus', 'aac', 'mp3']:
                    if best_audio_format is None or filesize > (best_audio_format.get('filesize') or 0):
                        best_audio_format = {
                            'id': format_id,
                            'quality': f"Audio / {ext.upper()}",
                            'fileSize': f"{round(filesize / (1024*1024), 2)} MB",
                            'format_type': "mp3", # Assume mp3 as general final audio type
                            'is_video_only': False
                        }
            
            # Find main video formats
            if vcodec and vcodec != 'none' and height in target_heights:
                # If we find a combined video+audio format for this resolution, prioritize it
                if acodec and acodec != 'none':
                    if height not in available_formats or available_formats[height].get('is_video_only', False):
                        available_formats[height] = {
                            'id': format_id,
                            'quality': f"Video / {height}p",
                            'fileSize': f"{round(filesize / (1024*1024), 2)} MB",
                            'format_type': "mp4", # Assume mp4 for video type
                            'is_video_only': False # This means it's combined
                        }
                # If it's video-only and we haven't found a combined format for this resolution yet, record it
                elif height not in available_formats: # Don't record video-only if a combined format already exists
                    available_formats[height] = {
                        'id': format_id,
                        'quality': f"Video / {height}p",
                        'fileSize': f"{round(filesize / (1024*1024), 2)} MB",
                        'format_type': "mp4",
                        'is_video_only': True # This means it's video-only and needs audio merging
                    }
                # If we already have a format for this resolution but the current format is better (larger file size)
                elif filesize > (available_formats[height].get('filesize') or 0):
                     # Always prefer combined format over video-only if available
                     if acodec and acodec != 'none':
                         available_formats[height] = {
                            'id': format_id,
                            'quality': f"Video / {height}p",
                            'fileSize': f"{round(filesize / (1024*1024), 2)} MB",
                            'format_type': "mp4",
                            'is_video_only': False
                        }
                     elif available_formats[height].get('is_video_only', False): # If current is video-only and new is video-only and larger
                         available_formats[height] = {
                            'id': format_id,
                            'quality': f"Video / {height}p",
                            'fileSize': f"{round(filesize / (1024*1024), 2)} MB",
                            'format_type': "mp4",
                            'is_video_only': True
                        }

        final_formats_list = []
        # Add video formats in descending order of resolution
        for h in target_heights:
            if h in available_formats:
                final_formats_list.append(available_formats[h])
        
        # Add the best audio format if found
        if best_audio_format:
            final_formats_list.append(best_audio_format)

        response_data = {
            "title": info.get('title'),
            "duration": info.get('duration'),
            "thumbnail": info.get('thumbnail'),
            "formats": final_formats_list
        }
        return jsonify(response_data), 200
    except yt_dlp.DownloadError as e:
        return jsonify({"error": f"Could not get video info: {str(e)}"}), 500
    except Exception as e:
        print(f"Server error in get_video_info: {e}")
        return jsonify({"error": "An internal server error occurred."}), 500

# Route to download video (with cutting and audio merging options)
@app.route('/download', methods=['POST'])
def download_video():
    data = request.json
    url = data.get('url')
    format_id = data.get('format_id')
    download_format = data.get('download_format') # 'mp4' or 'mp3'
    is_video_only = data.get('is_video_only', False) # Indicates if the selected format is video-only
    start_time_str = data.get('start_time') # HH:MM:SS
    end_time_str = data.get('end_time') # HH:MM:SS

    if not url or not format_id or not download_format:
        return jsonify({"error": "Missing URL, format_id, or download_format"}), 400

    temp_dir = 'temp_downloads'
    os.makedirs(temp_dir, exist_ok=True)

    # Sanitize title for filename
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'skip_download': True, 'dump_single_json': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'video')
            # Remove characters that are problematic in filenames
            # Also, replace spaces with underscores to avoid issues in shell commands
            sanitized_title = re.sub(r'[^\w\s.-]', '', video_title).strip().replace(' ', '_')
            # Limit length to avoid excessively long filenames
            sanitized_title = sanitized_title[:80] if len(sanitized_title) > 80 else sanitized_title
            sanitized_title = sanitized_title.rstrip('._-') # Remove trailing special chars
    except Exception as e:
        print(f"Error getting video title: {e}")
        sanitized_title = f"download_{int(time.time())}" # Fallback to a unique filename

    output_filename_base = os.path.join(temp_dir, sanitized_title)
    final_output_path = f"{output_filename_base}.{download_format}"
    
    # Clean up previous temp files for this video
    for f in os.listdir(temp_dir):
        if f.startswith(sanitized_title) and (f.endswith('.mp4') or f.endswith('.mp3') or f.endswith('.mkv') or f.endswith('.webm') or f.endswith('.m4a') or f.endswith('.opus') or f.endswith('.aac')):
            try:
                os.remove(os.path.join(temp_dir, f))
            except OSError as cleanup_error:
                print(f"Error cleaning up old temp file {f}: {cleanup_error}")

    temp_video_path = None
    temp_audio_path = None
    audio_extracted_path = None # New variable for extracted audio path

    try:
        # Step 1: Download video stream (and potentially audio if combined)
        ydl_opts_video = {
            'format': format_id,
            'outtmpl': f"{output_filename_base}_video.%(ext)s",
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts_video) as ydl_video:
            info_video = ydl_video.extract_info(url, download=True)
            temp_video_path = ydl_video.prepare_filename(info_video)
            
        print(f"Downloaded raw video file: {temp_video_path}")

        # If it's a video-only format, we need to download the best audio separately
        if is_video_only:
            print("Detected video-only format, downloading best audio...")
            audio_ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio[ext=opus]/bestaudio', # Prefer m4a or opus
                'outtmpl': f"{output_filename_base}_audio.%(ext)s",
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'extractaudio': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a', # Ensure consistency for merging
                }],
            }
            with yt_dlp.YoutubeDL(audio_ydl_opts) as ydl_audio:
                audio_info = ydl_audio.extract_info(url, download=True)
                # yt-dlp might change extension based on postprocessor
                downloaded_audio_path_base = ydl_audio.prepare_filename(audio_info)
                temp_audio_path = os.path.join(temp_dir, os.path.basename(downloaded_audio_path_base))
                
                # Check for actual extension change by postprocessor (e.g., webm to m4a)
                if audio_info.get('ext') == 'webm' and audio_ydl_opts['postprocessors'][0]['preferredcodec'] == 'm4a':
                     temp_audio_path = temp_audio_path.replace('.webm', '.m4a')
                
            print(f"Downloaded audio file: {temp_audio_path}")

        # Step 2: Merge video and audio if necessary (for is_video_only cases)
        current_processed_path = temp_video_path # Track the path of the most recent file
        
        if is_video_only and temp_video_path and temp_audio_path and os.path.exists(temp_video_path) and os.path.exists(temp_audio_path):
            merged_video_path = f"{output_filename_base}_merged.mp4"
            print(f"Merging video ({temp_video_path}) and audio ({temp_audio_path}) into {merged_video_path}...")
            
            merge_command = [
                'ffmpeg',
                '-i', temp_video_path,
                '-i', temp_audio_path,
                '-c:v', 'copy',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-shortest',
                '-strict', 'experimental',
                '-y',
                merged_video_path
            ]
            
            try:
                subprocess.run(merge_command, check=True, capture_output=True)
                print("Merge successful.")
                os.remove(temp_video_path)
                os.remove(temp_audio_path)
                current_processed_path = merged_video_path
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg merge error stdout: {e.stdout.decode()}")
                print(f"FFmpeg merge error stderr: {e.stderr.decode()}")
                raise Exception(f"Failed to merge video and audio: {e.stderr.decode().strip()}")
        elif is_video_only:
            raise Exception("Required video or audio file for merging was not found.")

        # --- New Logic for MP3 download path ---
        if download_format == 'mp3':
            print("Preparing for MP3 download: extracting audio...")
            audio_extracted_path = f"{output_filename_base}_extracted_audio.m4a" # Use m4a as a robust intermediate format
            
            extract_audio_command = [
                'ffmpeg',
                '-i', current_processed_path, # Input is the current video file (either original or merged)
                '-vn', # No video
                '-c:a', 'aac', # Re-encode to AAC for consistency before MP3 conversion
                '-b:a', '192k',
                '-map', '0:a:0', # Map only the audio stream
                '-y',
                audio_extracted_path
            ]
            try:
                subprocess.run(extract_audio_command, check=True, capture_output=True)
                print(f"Audio extraction successful: {audio_extracted_path}")
                # Now, current_processed_path points to the extracted audio file for trimming
                current_processed_path = audio_extracted_path
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg audio extraction error stdout: {e.stdout.decode()}")
                print(f"FFmpeg audio extraction error stderr: {e.stderr.decode()}")
                raise Exception(f"Failed to extract audio: {e.stderr.decode().strip()}")

        # Step 3: Trim the video/audio if start/end times are provided (IMPROVED VERSION)
        if start_time_str and end_time_str:
            def time_to_seconds(time_str):
                parts = list(map(int, time_str.split(':')))
                if len(parts) == 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2: return parts[0] * 60 + parts[1]
                return 0

            start_sec = time_to_seconds(start_time_str)
            end_sec = time_to_seconds(end_time_str)

            if end_sec <= start_sec:
                print("End time is before or same as start time, skipping trimming.")
            else:
                # تحديد امتداد الملف الحالي
                current_ext = os.path.splitext(current_processed_path)[1]
                trimmed_output_path = f"{output_filename_base}_trimmed{current_ext}"
                print(f"Trimming from {start_time_str} to {end_time_str}...")
                
                # حساب المدة بدلاً من استخدام -to
                duration = end_sec - start_sec
                
                if download_format == 'mp3':
                    # للصوت: إعادة ترميز لضمان الجودة والدقة
                    trim_command = [
                        'ffmpeg',
                        '-ss', start_time_str,  # وضع -ss قبل -i لتسريع العملية
                        '-i', current_processed_path,
                        '-t', str(duration),    # استخدام -t بدلاً من -to لتجنب مشاكل التوقيت
                        '-c:a', 'aac',          # إعادة ترميز الصوت
                        '-b:a', '192k',
                        '-avoid_negative_ts', 'make_zero',  # تجنب مشاكل الـ timestamps
                        '-y',
                        trimmed_output_path
                    ]
                else:
                    # للفيديو: حل هجين لتوازن السرعة والجودة
                    trim_command = [
                        'ffmpeg',
                        '-ss', start_time_str,  # البحث السريع قبل فتح الملف
                        '-i', current_processed_path,
                        '-t', str(duration),
                        '-c:v', 'libx264',      # إعادة ترميز الفيديو لحل مشاكل الـ keyframes
                        '-c:a', 'aac',          # إعادة ترميز الصوت
                        '-preset', 'fast',      # استخدام preset سريع
                        '-crf', '23',           # جودة جيدة مع حجم معقول
                        '-b:a', '192k',
                        '-avoid_negative_ts', 'make_zero',
                        '-movflags', '+faststart',  # تحسين للتشغيل السريع
                        '-y',
                        trimmed_output_path
                    ]
                
                try:
                    subprocess.run(trim_command, check=True, capture_output=True)
                    print("Trimming successful.")
                    os.remove(current_processed_path)
                    current_processed_path = trimmed_output_path
                except subprocess.CalledProcessError as e:
                    print(f"FFmpeg trim error stdout: {e.stdout.decode()}")
                    print(f"FFmpeg trim error stderr: {e.stderr.decode()}")
                    raise Exception(f"Failed to trim {'video' if download_format == 'mp4' else 'audio'}: {e.stderr.decode().strip()}")

        # Step 4: Convert to final desired format (MP3 if requested, else keep MP4)
        if download_format == 'mp3' and current_processed_path:
            print("Converting to MP3...")
            mp3_output_path = f"{output_filename_base}.mp3"
            convert_command = [
                'ffmpeg',
                '-i', current_processed_path, # Input is now guaranteed to be an audio-only file
                '-vn', # No video (redundant but harmless here)
                '-b:a', '192k',
                '-acodec', 'libmp3lame',
                '-y',
                mp3_output_path
            ]
            try:
                subprocess.run(convert_command, check=True, capture_output=True)
                print("MP3 conversion successful.")
                os.remove(current_processed_path) # Clean up audio file
                final_output_path = mp3_output_path
            except subprocess.CalledProcessError as e:
                print(f"FFmpeg MP3 conversion error stdout: {e.stdout.decode()}")
                print(f"FFmpeg MP3 conversion error stderr: {e.stderr.decode()}")
                raise Exception(f"Failed to convert to MP3: {e.stderr.decode().strip()}")
        else:
            # If not converting to MP3, the last processed video is the final
            final_output_path = current_processed_path

        if not os.path.exists(final_output_path):
            raise Exception("Final output file was not created or found.")

        response = send_file(final_output_path, as_attachment=True, download_name=os.path.basename(final_output_path))
        
        # Clean up files after sending
        @response.call_on_close
        def cleanup():
            print(f"Cleaning up {final_output_path}")
            # Ensure proper cleanup of all temp files created during the process
            temp_files_to_check = [
                f"{output_filename_base}_video",
                f"{output_filename_base}_audio",
                f"{output_filename_base}_merged",
                f"{output_filename_base}_extracted_audio",
                f"{output_filename_base}_trimmed",
                f"{output_filename_base}"
            ]
            
            for f_base in temp_files_to_check:
                for ext in ['mp4', 'mkv', 'webm', 'mp3', 'm4a', 'opus', 'aac']:
                    temp_file = f"{f_base}.{ext}"
                    if os.path.exists(temp_file):
                        try:
                            os.remove(temp_file)
                            print(f"Removed temp file: {temp_file}")
                        except OSError as cleanup_error:
                            print(f"Error during cleanup of {temp_file}: {cleanup_error}")

        return response

    except Exception as e:
        print(f"Download/processing error: {e}")
        # Attempt to clean up any created temp files if an error occurs
        for f in os.listdir(temp_dir):
            if f.startswith(sanitized_title):
                try:
                    os.remove(os.path.join(temp_dir, f))
                except OSError as cleanup_error:
                    print(f"Error during error cleanup of {f}: {cleanup_error}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)