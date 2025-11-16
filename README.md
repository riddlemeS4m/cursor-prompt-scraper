# cursor-prompt-scraper

A proxy-based logger for Cursor API requests with MongoDB storage and intelligent de-duplication.

## Features

- **Multi-format Logging**: Captures API requests in multiple formats (raw, binary, clean text, JSON)
- **MongoDB Integration**: Stores structured data with strict de-duplication based on session ID
- **Smart De-duplication**: Prevents duplicate entries using hash-based comparison of extracted texts and JSON objects
- **Production-Ready**: Environment variable controls for file logging and console output
- **Session Tracking**: Automatic session start/end logging with statistics

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables (copy `env.example` to `.env`):
```bash
cp env.example .env
```

3. Edit `.env` with your configuration:
   - MongoDB connection details (if using MongoDB)
   - Logging preferences (enable/disable file and console logging)

## Environment Variables

### MongoDB Configuration
- `MONGO_HOST`: MongoDB host (default: `localhost`)
- `MONGO_PORT`: MongoDB port (default: `27017`)
- `MONGO_DB`: Database name (default: `prompt_engineering`)
- `MONGO_COLLECTION`: Collection name (default: `api_requests`)
- `MONGO_TIMEOUT_MS`: Connection timeout (default: `5000`)
- `MONGO_USERNAME`: MongoDB username (optional)
- `MONGO_PASSWORD`: MongoDB password (optional)
- `MONGO_AUTH_DB`: Authentication database (default: `admin`)

### Logging Configuration
- `ENABLE_FILE_LOGGING`: Enable/disable file logging (default: `true`)
  - Set to `false`, `0`, `no`, or `off` to disable in production
- `ENABLE_CONSOLE_LOGGING`: Enable/disable console output (default: `true`)
  - Set to `false`, `0`, `no`, or `off` to disable in production

## Production Configuration

For production environments where you want to minimize disk I/O and console output while still using MongoDB:

```env
# Disable file logging to save disk space
ENABLE_FILE_LOGGING=false

# Disable console logging to reduce noise
ENABLE_CONSOLE_LOGGING=false

# MongoDB remains active for data storage
MONGO_HOST=your-mongo-server
MONGO_PORT=27017
```

## De-duplication Logic

The system prevents duplicate data from being stored in MongoDB:

1. For each API request, the system extracts:
   - All text content from the JSON objects
   - The complete JSON object structure

2. Creates deterministic hashes for:
   - All extracted texts (combined and sorted)
   - All JSON objects (with sorted keys)

3. Before inserting, checks if a document with:
   - Same `session_id`
   - Same `text_hash`
   - Same `json_hash`
   already exists

4. Only inserts if no duplicate is found

This ensures that identical requests within a session are only stored once, dramatically reducing database load and storage requirements.

## Usage

Run with mitmproxy:
```bash
mitmdump -s logger.py
```

Then configure your application to use the proxy (typically `localhost:8080`).

## Statistics

At the end of each session, the logger displays statistics:
- Total requests logged
- Unique requests saved to MongoDB
- Duplicates prevented

## File Structure

- `logger.py`: Main proxy logger with mitmproxy integration
- `mongo_client.py`: Dedicated MongoDB client with de-duplication logic
- `logs/`: Directory containing log files (when file logging is enabled)
  - `raw_*.log`: Raw UTF-8 decoded requests
  - `binary_*.bin`: Pure binary protobuf data
  - `clean_*.log`: Filtered printable text only
  - `json_*.log`: Extracted and parsed JSON objects