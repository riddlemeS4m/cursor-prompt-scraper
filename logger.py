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
from dotenv import load_dotenv
from mitmproxy import http
from pathlib import Path
from typing import List, Dict, Any
from mongo_client import MongoDBClient

# Load environment variables
load_dotenv()

# Logging Configuration
ENABLE_FILE_LOGGING = os.getenv("ENABLE_FILE_LOGGING", "true").lower() in ("true", "1", "yes", "on")
ENABLE_CONSOLE_LOGGING = os.getenv("ENABLE_CONSOLE_LOGGING", "true").lower() in ("true", "1", "yes", "on")

class CursorLogger:
    def __init__(self):
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        self.session_start = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.raw_log = self.log_dir / f"raw_{self.session_start}.log"
        self.binary_log = self.log_dir / f"binary_{self.session_start}.bin"
        self.clean_log = self.log_dir / f"clean_{self.session_start}.log"
        self.json_log = self.log_dir / f"json_{self.session_start}.log"
        self.request_count = 0
        
        # Logging configuration
        self.file_logging_enabled = ENABLE_FILE_LOGGING
        self.console_logging_enabled = ENABLE_CONSOLE_LOGGING
        
        # Regex used to extract JSON objects (lenient, dotall)
        self.json_regex_str = r'(?s)\{\s*"root"\s*:\s*\{.*?\}\s*\}'
        self.json_regex = re.compile(self.json_regex_str, re.DOTALL)
        
        # Initialize MongoDB client with de-duplication logic
        self.mongo_client = MongoDBClient()
        
        self._log(f"\n🚀 Cursor API Logger Started")
        self._log(f"📁 Session: {self.session_start}")
        self._log(f"📄 File logging: {'ENABLED' if self.file_logging_enabled else 'DISABLED'}")
        self._log(f"💬 Console logging: {'ENABLED' if self.console_logging_enabled else 'DISABLED'}")
        
        if self.file_logging_enabled:
            self._log(f"📁 Raw log: {self.raw_log}")
            self._log(f"📁 Binary log: {self.binary_log} (pure protobuf)")
            self._log(f"📁 Clean log: {self.clean_log}")
            self._log(f"📁 JSON log: {self.json_log}")
        
        # Try to connect to MongoDB and log session start
        if self.mongo_client.connect():
            self.mongo_client.log_session_start(self.session_start)
        
        self._log("-" * 50)
    
    def _log(self, message: str):
        """Conditional console logging based on environment variable"""
        if self.console_logging_enabled:
            print(message)
    
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
                        json_objects: List[Dict[str, Any]], raw_text: str, endpoint: str):
        """Save request data to MongoDB with strict de-duplication"""
        if not self.mongo_client.connected:
            return
        
        try:
            # Extract texts from each JSON object
            extracted_texts = []
            for idx, obj in enumerate(json_objects):
                texts = self.extract_text_from_cursor_json(obj)
                if texts:
                    extracted_texts.append({
                        "object_index": idx,
                        "texts": texts
                    })
            
            # Use MongoDB client's insert_request method with de-duplication
            result = self.mongo_client.insert_request(
                session_id=self.session_start,
                request_num=request_num,
                timestamp=timestamp,
                json_objects=json_objects,
                extracted_texts=extracted_texts,
                raw_size_bytes=len(raw_text),
                endpoint=endpoint
            )
            
            if result is None:
                self._log(f"   ⏭️  Duplicate detected - NOT saved to MongoDB")
            
        except Exception as e:
            self._log(f"   ⚠️  MongoDB save failed: {e}")
            # Don't crash the proxy if MongoDB fails
    
    def request(self, flow: http.HTTPFlow) -> None:
        """Process intercepted HTTP requests with focused debugging"""
        # First, log ALL Cursor API requests to help discover new endpoints
        full_url = f"{flow.request.pretty_host}{flow.request.path}"
        
        # Check if this is a Cursor API request
        if "api2.cursor.sh" in flow.request.pretty_host or "cursor.sh" in flow.request.pretty_host:
            # Print summary of ALL Cursor endpoints to help discovery
            self._log(f"\n🔍 Cursor API Request Detected:")
            self._log(f"   Host: {flow.request.pretty_host}")
            self._log(f"   Path: {flow.request.path}")
            self._log(f"   Method: {flow.request.method}")
            self._log(f"   Size: {len(flow.request.content)} bytes")
            
            # Check if this looks like a chat/AI endpoint (more flexible matching)
            is_chat_endpoint = any(keyword in flow.request.path.lower() for keyword in [
                'chat', 'stream', 'unified', 'warmstream', 'aiserver'
            ])
            
            if is_chat_endpoint:
                self.request_count += 1
                timestamp = datetime.datetime.now()
                endpoint = flow.request.path  # Store the actual endpoint path
                
                self._log(f"\n🔥 LOGGING REQUEST #{self.request_count} at {timestamp.strftime('%H:%M:%S')}")
                self._log(f"   Endpoint: {endpoint}")
                if self.file_logging_enabled:
                    self._log(f"   💾 Saving: raw (UTF-8), binary (protobuf), clean, JSON")
                
                # Decode the request body
                try:
                    raw_text = flow.request.content.decode('utf-8', errors='ignore')
                except Exception as e:
                    self._log(f"   ❌ Error decoding: {e}")
                    return
            else:
                self._log(f"   ⏩ Skipping (not a chat endpoint)")
                return
        else:
            # Not a Cursor API request, skip silently
            return
        
        # DEBUG: Check raw text content
        self._log(f"   🔍 Raw text length: {len(raw_text)}")
        self._log(f"   🔍 Raw text hash: {hash(raw_text)}")  # Simple hash to verify uniqueness
        self._log(f"   🔍 First 200 chars: {raw_text[:200]}")
        self._log(f"   🔍 Last 200 chars: {raw_text[-200:]}")
        
        # File logging (conditional based on environment variable)
        if self.file_logging_enabled:
            # 1. Save RAW log (decoded UTF-8 with errors ignored)
            with open(self.raw_log, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"REQUEST #{self.request_count}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"Endpoint: {endpoint}\n")
                f.write(f"Full URL: {flow.request.pretty_host}{flow.request.path}\n")
                f.write(f"\nRAW DATA:\n")
                f.write(raw_text)
                f.write(f"\n{'='*80}\n\n")
            
            # 1b. Save BINARY log (pure protobuf bytes - no decoding!)
            with open(self.binary_log, 'ab') as f:
                # Write metadata header
                header = f"\n{'='*80}\n".encode('utf-8')
                header += f"REQUEST #{self.request_count}\n".encode('utf-8')
                header += f"Timestamp: {timestamp}\n".encode('utf-8')
                header += f"Size: {len(flow.request.content)} bytes\n".encode('utf-8')
                header += f"{'='*80}\n".encode('utf-8')
                f.write(header)
                
                # Write pure binary protobuf data
                f.write(flow.request.content)
                
                # Write footer
                footer = f"\n{'='*80}\n\n".encode('utf-8')
                f.write(footer)
            
            # 2. Save CLEAN log (working correctly)
            clean_text = self.filter_printable(raw_text)
            self._log(f"   📝 Clean text length: {len(clean_text)}")
            
            with open(self.clean_log, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"REQUEST #{self.request_count}\n")
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"\nCLEAN TEXT (printable only):\n")
                f.write(clean_text)
                f.write(f"\n{'='*80}\n\n")
            
        # Extract JSON objects (always needed for MongoDB)
        self._log(f"\n   🔍 Starting JSON extraction via regex...")
        self._log(f"   🔍 Input text length: {len(raw_text)}")
        self._log(f"   🔍 Using pattern: {self.json_regex_str}")
        
        json_objects = []
        matches = self.json_regex.findall(raw_text)
        self._log(f"   🔎 Regex found {len(matches)} potential JSON block(s)")
        
        for mi, m in enumerate(matches, 1):
            preview = m[:100] + ('...' if len(m) > 100 else '')
            self._log(f"      Candidate #{mi} length={len(m)} preview={preview!r}")
            try:
                obj = json.loads(m)
                json_objects.append(obj)
                self._log(f"      ✅ Candidate #{mi} parsed as valid JSON")
            except Exception as e:
                self._log(f"      ❌ Candidate #{mi} invalid JSON: {e}")
                continue
        
        self._log(f"   📦 Extracted {len(json_objects)} valid JSON object(s) from {len(matches)} match(es)")
        
        # Write extracted JSON to the JSON log file (conditional)
        if self.file_logging_enabled:
            with open(self.json_log, 'a', encoding='utf-8') as jf:
                jf.write(f"\n{'='*80}\n")
                jf.write(f"REQUEST #{self.request_count}\n")
                jf.write(f"Timestamp: {timestamp}\n")
                jf.write(f"Regex pattern: {self.json_regex_str}\n")
                jf.write(f"Valid JSON objects: {len(json_objects)}\n\n")
                for idx, obj in enumerate(json_objects, 1):
                    jf.write(f"-- Object #{idx} --\n")
                    jf.write(json.dumps(obj, ensure_ascii=False, indent=2))
                    jf.write("\n\n")
                jf.write(f"{'='*80}\n\n")
        
        # Post-process: extract texts
        all_texts = []
        for obj in json_objects:
            texts = self.extract_text_from_cursor_json(obj)
            all_texts.extend(texts)
        
        self._log(f"   📝 Total texts extracted: {len(all_texts)}")
        if all_texts:
            self._log(f"   📝 First text: {all_texts[0][:100] if all_texts[0] else 'empty'}")
        
        # Save structured data to MongoDB (if connected)
        self.save_to_mongodb(self.request_count, timestamp, json_objects, raw_text, endpoint)
    
    def done(self):
        """Cleanup when the proxy stops"""
        if self.mongo_client.connected:
            try:
                # Log session end
                self.mongo_client.log_session_end(self.session_start, self.request_count)
                
                # Get and print session statistics
                stats = self.mongo_client.get_session_stats(self.session_start)
                if stats:
                    self._log(f"\n📊 Session ended. Statistics:")
                    self._log(f"   Total requests logged: {stats.get('total_requests', 0)}")
                    self._log(f"   Unique requests saved: {stats.get('unique_requests', 0)}")
                    self._log(f"   Duplicates prevented: {stats.get('duplicates_prevented', 0)}")
                else:
                    self._log(f"\n📊 Session ended. Total requests logged: {self.request_count}")
            except Exception as e:
                self._log(f"\n⚠️  Error during cleanup: {e}")
            finally:
                self.mongo_client.close()

# mitmproxy addon
addons = [CursorLogger()]