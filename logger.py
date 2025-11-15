#!/usr/bin/env python3
"""
Enhanced Cursor API Logger with MongoDB storage
Intercepts and logs Cursor API requests to both files and MongoDB
"""

import datetime
import json
import os
import re
import string
from mitmproxy import http
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from typing import Optional, List, Dict, Any

# Configuration from environment variables
MONGO_URI = os.getenv("MONGO_URI", "mongodb://sam-desktop:27017")
MONGO_DB = os.getenv("MONGO_DB", "cursor_logs")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "api_requests")
MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "5000"))
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

class CursorLogger:
    def __init__(self):
        self.log_dir = Path("/app/logs")  # Docker volume mount
        self.log_dir.mkdir(exist_ok=True)
        self.session_start = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_log = self.log_dir / f"raw_{self.session_start}.log"
        self.binary_log = self.log_dir / f"binary_{self.session_start}.bin"
        self.clean_log = self.log_dir / f"clean_{self.session_start}.log"
        self.json_log = self.log_dir / f"json_{self.session_start}.log"
        self.request_count = 0
        
        # Regex used to extract JSON objects (lenient, dotall)
        self.json_regex_str = r'(?s)\{\s*"root"\s*:\s*\{.*?\}\s*\}'
        self.json_regex = re.compile(self.json_regex_str, re.DOTALL)
        
        # Initialize MongoDB connection
        self.mongo_client: Optional[MongoClient] = None
        self.mongo_collection = None
        self.mongo_connected = False
        
        if not PRODUCTION_MODE:
            print(f"\n🚀 Cursor API Logger Started")
            print(f"📁 Session: {self.session_start}")
            print(f"📁 Log directory: {self.log_dir}")
            print(f"🔧 MongoDB URI: {MONGO_URI}")
            print(f"🔧 Production mode: {PRODUCTION_MODE}")
        
        # Try to connect to MongoDB
        self._connect_mongodb()
        if not PRODUCTION_MODE:
            print("-" * 50)
    
    def _connect_mongodb(self):
        """Establish connection to MongoDB"""
        try:
            if not PRODUCTION_MODE:
                print(f"\n🔗 Connecting to MongoDB: {MONGO_URI}...")
            
            self.mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=MONGO_TIMEOUT_MS
            )
            
            # Test connection
            self.mongo_client.admin.command('ping')
            
            # Get database and collection
            db = self.mongo_client[MONGO_DB]
            self.mongo_collection = db[MONGO_COLLECTION]
            
            self.mongo_connected = True
            if not PRODUCTION_MODE:
                print(f"✅ MongoDB connected! Database: {MONGO_DB}, Collection: {MONGO_COLLECTION}")
            
            # Log session start
            self.mongo_collection.insert_one({
                "type": "session_start",
                "session_id": self.session_start,
                "timestamp": datetime.datetime.now(),
                "source": "cursor_logger"
            })
            
        except (ServerSelectionTimeoutError, ConnectionFailure) as e:
            print(f"⚠️  MongoDB connection failed: {e}")
            print("   Continuing with file logging only...")
            self.mongo_connected = False
        except Exception as e:
            print(f"⚠️  Unexpected MongoDB error: {e}")
            self.mongo_connected = False
    
    def filter_printable(self, text: str) -> str:
        """Filter out non-printable characters, keeping only readable text"""
        return ''.join(c for c in text if c in string.printable)
    
    def extract_json_objects(self, text: str) -> List[Dict[str, Any]]:
        """Extract JSON objects from the text using brace matching"""
        json_objects = []
        depth = 0
        start_idx = None
        
        for i, char in enumerate(text):
            if char == '{':
                if depth == 0:
                    start_idx = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and start_idx is not None:
                    json_str = text[start_idx:i+1]
                    try:
                        obj = json.loads(json_str)
                        json_objects.append(obj)
                    except:
                        pass
                    start_idx = None
        
        return json_objects
    
    def extract_text_from_cursor_json(self, obj: Dict[str, Any]) -> List[str]:
        """Extract readable text from Cursor's JSON format"""
        texts = []
        
        def recurse(node):
            if isinstance(node, dict):
                # Check if this is a text node
                if node.get('type') == 'text' and 'text' in node:
                    texts.append(node['text'])
                # Recurse into children
                for key, value in node.items():
                    recurse(value)
            elif isinstance(node, list):
                for item in node:
                    recurse(item)
        
        recurse(obj)
        return texts
    
    def save_to_mongodb(self, request_num: int, timestamp: datetime.datetime, 
                        json_objects: List[Dict[str, Any]], raw_text: str):
        """Save request data to MongoDB"""
        if not self.mongo_connected or self.mongo_collection is None:
            return
        
        try:
            # Prepare document
            doc = {
                "session_id": self.session_start,
                "request_number": request_num,
                "timestamp": timestamp,
                "type": "api_request",
                "endpoint": "aiserver.v1.ChatService/WarmStreamUnifiedChatWithTools",
                "json_objects_count": len(json_objects),
                "json_objects": json_objects,
                "extracted_texts": [],
                "raw_size_bytes": len(raw_text)
            }
            
            # Extract texts from each JSON object
            for idx, obj in enumerate(json_objects):
                texts = self.extract_text_from_cursor_json(obj)
                if texts:
                    doc["extracted_texts"].append({
                        "object_index": idx,
                        "texts": texts
                    })
            
            # Insert into MongoDB
            result = self.mongo_collection.insert_one(doc)
            if not PRODUCTION_MODE:
                print(f"   💾 Saved to MongoDB (ID: {result.inserted_id})")
            
        except Exception as e:
            print(f"   ⚠️  MongoDB save failed: {e}")
    
    def request(self, flow: http.HTTPFlow) -> None:
        """Process intercepted HTTP requests"""
        target_endpoint = "api2.cursor.sh/aiserver.v1.ChatService/WarmStreamUnifiedChatWithTools"
        
        if target_endpoint in f"{flow.request.pretty_host}{flow.request.path}":
            self.request_count += 1
            timestamp = datetime.datetime.now()
            
            # Minimal logging in production
            if PRODUCTION_MODE:
                print(f"[{timestamp.strftime('%H:%M:%S')}] Request #{self.request_count} ({len(flow.request.content)} bytes)")
            else:
                print(f"\n🔥 REQUEST #{self.request_count} at {timestamp.strftime('%H:%M:%S')}")
                print(f"   Size: {len(flow.request.content)} bytes")
            
            # Decode the request body
            try:
                raw_text = flow.request.content.decode('utf-8', errors='ignore')
            except Exception as e:
                print(f"   ❌ Error decoding: {e}")
                return
            
            # 1. Save RAW log
            with open(self.raw_log, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"REQUEST #{self.request_count}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Endpoint: {flow.request.pretty_host}{flow.request.path}\n")
                f.write(f"\nRAW DATA:\n")
                f.write(raw_text)
                f.write(f"\n{'='*80}\n\n")
            
            # 2. Save BINARY log
            with open(self.binary_log, 'ab') as f:
                header = f"\n{'='*80}\n".encode('utf-8')
                header += f"REQUEST #{self.request_count}\n".encode('utf-8')
                header += f"Timestamp: {timestamp}\n".encode('utf-8')
                header += f"Size: {len(flow.request.content)} bytes\n".encode('utf-8')
                header += f"{'='*80}\n".encode('utf-8')
                f.write(header)
                f.write(flow.request.content)
                footer = f"\n{'='*80}\n\n".encode('utf-8')
                f.write(footer)
            
            # 3. Save CLEAN log
            clean_text = self.filter_printable(raw_text)
            with open(self.clean_log, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"REQUEST #{self.request_count}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"\nCLEAN TEXT (printable only):\n")
                f.write(clean_text)
                f.write(f"\n{'='*80}\n\n")
            
            # 4. Extract JSON objects
            json_objects = []
            matches = self.json_regex.findall(raw_text)
            
            if not PRODUCTION_MODE:
                print(f"   🔎 Regex found {len(matches)} potential JSON block(s)")
            
            for m in matches:
                try:
                    obj = json.loads(m)
                    json_objects.append(obj)
                except:
                    continue
            
            # 5. Write extracted JSON
            with open(self.json_log, 'a', encoding='utf-8') as jf:
                jf.write(f"\n{'='*80}\n")
                jf.write(f"REQUEST #{self.request_count}\n")
                jf.write(f"Timestamp: {timestamp}\n")
                jf.write(f"Valid JSON objects: {len(json_objects)}\n\n")
                for idx, obj in enumerate(json_objects, 1):
                    jf.write(f"-- Object #{idx} --\n")
                    jf.write(json.dumps(obj, ensure_ascii=False, indent=2))
                    jf.write("\n\n")
                jf.write(f"{'='*80}\n\n")
            
            # 6. Save to MongoDB
            self.save_to_mongodb(self.request_count, timestamp, json_objects, raw_text)
    
    def done(self):
        """Cleanup when the proxy stops"""
        if self.mongo_connected and self.mongo_collection:
            try:
                self.mongo_collection.insert_one({
                    "type": "session_end",
                    "session_id": self.session_start,
                    "timestamp": datetime.datetime.now(),
                    "total_requests": self.request_count
                })
                print(f"\n📊 Session ended. Total requests logged: {self.request_count}")
            except:
                pass
            finally:
                if self.mongo_client:
                    self.mongo_client.close()

# mitmproxy addon
addons = [CursorLogger()]
