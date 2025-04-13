import praw
import os
import re
import json
from datetime import datetime
from gtts import gTTS
import logging
from pathlib import Path

# --- Configuration ---
CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
USER_AGENT = os.environ.get("REDDIT_USER_AGENT")
# Default User Agent if not set as a secret
if not USER_AGENT:
    USER_AGENT = "linux:mysecretpodcastbot:v1.0 (by /u/YourRedditUsername)"  # CHANGE THIS

# Configuration settings
TARGET_SUBREDDIT = "SluttyConfessions"  # Or "TrueOffMyChest", "secrets", etc.
POST_LIMIT = 10  # Number of posts to fetch each run
MIN_TEXT_LENGTH = 50  # Minimum characters in post body to process
MAX_TEXT_LENGTH = 5000  # Maximum characters to avoid very long TTS generations
AUDIO_DIR = "audio"
HTML_FILE = "index.html"
HISTORY_FILE = "processed_posts.json"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# --- Helper Functions ---
def sanitize_filename(name):
    """Remove invalid characters for filenames and limit length."""
    # Replace invalid characters with underscores
    name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores
    name = name.strip('_')
    # Limit length to 100 characters
    return name[:100]


def load_processed_posts():
    """Load the list of previously processed post IDs."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading processed posts: {e}")
            return set()
    return set()


def save_processed_posts(processed_ids):
    """Save the list of processed post IDs."""
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(list(processed_ids), f)
    except IOError as e:
        logger.error(f"Error saving processed posts: {e}")


def generate_tts(text, filename):
    """Generate TTS audio file using gTTS with error handling and retries."""
    # Limit text length if necessary
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning(f"Text too long ({len(text)} chars). Truncating to {MAX_TEXT_LENGTH} chars.")
        text = text[:MAX_TEXT_LENGTH] + "... [Content truncated due to length]"
    
    # Ensure audio directory exists
    Path(AUDIO_DIR).mkdir(exist_ok=True)
    filepath = os.path.join(AUDIO_DIR, filename)
    
    # Try up to 3 times in case of network issues
    for attempt in range(3):
        try:
            tts = gTTS(text=text, lang='en', slow=False)
            tts.save(filepath)
            logger.info(f"Successfully generated TTS: {filename}")
            return filename
        except Exception as e:
            logger.warning(f"TTS generation attempt {attempt+1} failed: {e}")
            if attempt == 2:  # Last attempt
                logger.error(f"Failed to generate TTS after 3 attempts for '{filename}'")
                return None
    
    return None


def connect_to_reddit():
    """Establish connection to Reddit API with proper error handling."""
    if not all([CLIENT_ID, CLIENT_SECRET, USER_AGENT]):
        logger.error("Reddit API credentials not found in environment variables.")
        logger.error("Please set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT as GitHub Secrets.")
        return None

    logger.info(f"Connecting to Reddit API...")
    try:
        reddit = praw.Reddit(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            user_agent=USER_AGENT,
        )
        # Test connection (this will raise an exception if there's an issue)
        reddit.user.me()  # Can be None for read-only access
        logger.info("Successfully connected to Reddit API")
        return reddit
    except Exception as e:
        logger.error(f"Failed to connect to Reddit API: {e}")
        return None


def fetch_and_process_posts(reddit, processed_ids):
    """Fetch posts from Reddit and generate audio files."""
    posts_data = []
    
    try:
        logger.info(f"Fetching top {POST_LIMIT} posts from r/{TARGET_SUBREDDIT}")
        subreddit = reddit.subreddit(TARGET_SUBREDDIT)
        
        for submission in subreddit.hot(limit=POST_LIMIT):
            # Skip posts that don't meet our criteria
            if (submission.id in processed_ids or
                submission.stickied or
                submission.is_self == False or  # Must be a text post
                not submission.selftext or  # Must have body text
                len(submission.selftext) < MIN_TEXT_LENGTH):
                logger.debug(f"Skipping post {submission.id}: does not meet criteria")
                continue
                
            logger.info(f"Processing post: {submission.id} - {submission.title[:50]}...")
            
            # Create a clean title and prepare the text for TTS
            clean_title = submission.title.strip()
            full_text = f"Title: {clean_title}. Story: {submission.selftext}"
            
            # Generate filename based on post ID and sanitized title
            base_filename = f"{submission.id}_{sanitize_filename(clean_title)}"
            audio_filename = f"{base_filename}.mp3"
            audio_path = os.path.join(AUDIO_DIR, audio_filename)
            
            # Check if audio already exists
            if os.path.exists(audio_path):
                logger.info(f"Audio file already exists for post {submission.id}, using existing file")
                audio_file = audio_filename
            else:
                # Generate new audio file
                audio_file = generate_tts(full_text, audio_filename)
                if not audio_file:
                    logger.warning(f"Skipping post {submission.id} due to TTS error")
                    continue
            
            # Add to our list of processed posts
            posts_data.append({
                "id": submission.id,
                "title": clean_title,
                "text": submission.selftext,
                "audio_file": audio_file,
                "date": submission.created_utc,
                "url": f"https://www.reddit.com{submission.permalink}"
            })
            processed_ids.add(submission.id)
            
    except praw.exceptions.PRAWException as e:
        logger.error(f"Reddit API Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching posts: {e}")
    
    return posts_data, processed_ids


def generate_html(posts_data):
    """Generate the HTML page with the posts and audio players."""
    logger.info(f"Generating {HTML_FILE}...")
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>r/{TARGET_SUBREDDIT} Narrated</title>
    <style>
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            line-height: 1.6; 
            padding: 20px; 
            max-width: 800px; 
            margin: auto; 
            background-color: #f5f5f5; 
            color: #333; 
        }}
        header {{ 
            text-align: center; 
            margin-bottom: 30px; 
            padding-bottom: 20px; 
            border-bottom: 1px solid #ddd; 
        }}
        .post {{ 
            background-color: #fff; 
            border: 1px solid #ddd; 
            border-radius: 8px; 
            margin-bottom: 25px; 
            padding: 20px; 
            box-shadow: 0 2px 4px rgba(0,0,0,0.1); 
            transition: transform 0.2s ease; 
        }}
        .post:hover {{ 
            transform: translateY(-3px); 
            box-shadow: 0 4px 8px rgba(0,0,0,0.15); 
        }}
        h1 {{ 
            color: #2c3e50; 
        }}
        h2 {{ 
            margin-top: 0; 
            color: #3498db; 
            font-size: 1.5rem; 
        }}
        audio {{ 
            width: 100%; 
            margin: 15px 0; 
            border-radius: 30px; 
        }}
        details {{ 
            margin-top: 15px; 
            cursor: pointer; 
        }}
        summary {{ 
            font-weight: bold; 
            color: #555; 
            padding: 5px 0; 
        }}
        p {{ 
            white-space: pre-wrap; /* Preserve line breaks */ 
            margin-top: 10px; 
            line-height: 1.7; 
        }}
        .meta {{ 
            font-size: 0.85rem; 
            color: #888; 
            margin-top: 15px; 
            text-align: right; 
        }}
        .meta a {{ 
            color: #3498db; 
            text-decoration: none; 
        }}
        .meta a:hover {{ 
            text-decoration: underline; 
        }}
        footer {{ 
            text-align: center; 
            margin-top: 40px; 
            padding-top: 20px; 
            border-top: 1px solid #ddd; 
            font-size: 0.9rem; 
            color: #888; 
        }}
    </style>
</head>
<body>
    <header>
        <h1>r/{TARGET_SUBREDDIT} Narrated</h1>
        <p>Latest posts with text-to-speech audio. Generated automatically.</p>
    </header>
"""

    if not posts_data:
        html_content += "<p>No new posts found matching criteria in this run.</p>"
    else:
        # Sort by date (newest first)
        posts_data.sort(key=lambda x: x.get('date', 0), reverse=True)
        
        for post in posts_data:
            # Format date if available
            date_str = ""
            if 'date' in post:
                try:
                    date_obj = datetime.fromtimestamp(post['date'])
                    date_str = date_obj.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    date_str = "Unknown date"
            
            html_content += f"""
    <div class="post" id="post-{post['id']}">
        <h2>{post['title']}</h2>
        <audio controls src="{AUDIO_DIR}/{post['audio_file']}">
            Your browser does not support the audio element.
        </audio>
        <details>
            <summary>Show/Hide Text</summary>
            <p>{post['text']}</p>
        </details>
        <div class="meta">
            Posted: {date_str}
            {f' | <a href="{post["url"]}" target="_blank" rel="noopener noreferrer">Original Post</a>' if 'url' in post else ''}
        </div>
    </div>
"""

    # Add footer with generation time
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_content += f"""
    <footer>
        <p>Last updated: {current_time}</p>
        <p>Content sourced from <a href="https://www.reddit.com/r/{TARGET_SUBREDDIT}" target="_blank">r/{TARGET_SUBREDDIT}</a></p>
    </footer>
</body>
</html>
"""

    try:
        with open(HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Successfully generated {HTML_FILE}")
        return True
    except IOError as e:
        logger.error(f"Failed to write {HTML_FILE}: {e}")
        return False


def main():
    """Main program execution."""
    # Connect to Reddit
    reddit = connect_to_reddit()
    if not reddit:
        return False
    
    # Load list of already processed posts
    processed_ids = load_processed_posts()
    logger.info(f"Loaded {len(processed_ids)} previously processed post IDs")
    
    # Fetch and process posts
    posts_data, processed_ids = fetch_and_process_posts(reddit, processed_ids)
    logger.info(f"Processed {len(posts_data)} new posts")
    
    # Save updated list of processed posts
    save_processed_posts(processed_ids)
    
    # Generate HTML with all posts
    success = generate_html(posts_data)
    
    return success


if __name__ == "__main__":
    success = main()
    if not success:
        logger.error("Program execution failed")
        exit(1)
    logger.info("Program completed successfully")
