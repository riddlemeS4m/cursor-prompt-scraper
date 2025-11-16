#!/usr/bin/env python3
"""
MongoDB Client with strict de-duplication logic
Prevents duplicate entries based on session_id + extracted_text + json_objects
"""

import datetime
import json
import os
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MongoDB Configuration from environment variables
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB = os.getenv("MONGO_DB", "prompt_engineering")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "api_requests")
MONGO_TIMEOUT_MS = int(os.getenv("MONGO_TIMEOUT_MS", "5000"))
MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_AUTH_DB = os.getenv("MONGO_AUTH_DB", "admin")

# Logging Configuration
ENABLE_CONSOLE_LOGGING = os.getenv("ENABLE_CONSOLE_LOGGING", "true").lower() in ("true", "1", "yes", "on")


class MongoDBClient:
    """MongoDB client with strict de-duplication based on session_id"""
    
    def __init__(self):
        self.client: Optional[MongoClient] = None
        self.collection = None
        self.connected = False
        self.db_name = MONGO_DB
        self.collection_name = MONGO_COLLECTION
        self.console_logging_enabled = ENABLE_CONSOLE_LOGGING
    
    def _log(self, message: str):
        """Conditional console logging based on environment variable"""
        if self.console_logging_enabled:
            print(message)
    
    def connect(self) -> bool:
        """Establish connection to MongoDB and create indexes"""
        try:
            self._log(f"\n🔗 Connecting to MongoDB at {MONGO_HOST}:{MONGO_PORT}...")
            
            # Build connection parameters
            connection_params = {
                "host": MONGO_HOST,
                "port": MONGO_PORT,
                "serverSelectionTimeoutMS": MONGO_TIMEOUT_MS
            }
            
            # Add authentication if credentials are provided
            if MONGO_USERNAME and MONGO_PASSWORD:
                connection_params["username"] = MONGO_USERNAME
                connection_params["password"] = MONGO_PASSWORD
                connection_params["authSource"] = MONGO_AUTH_DB
                self._log(f"   🔐 Using authentication: username={MONGO_USERNAME}, authSource={MONGO_AUTH_DB}")
            
            self.client = MongoClient(**connection_params)
            
            # Test connection
            self.client.admin.command('ping')
            
            # Get database and collection
            db = self.client[MONGO_DB]
            self.collection = db[MONGO_COLLECTION]
            
            # Create indexes for efficient de-duplication queries
            self._create_indexes()
            
            self.connected = True
            self._log(f"✅ MongoDB connected! Database: {MONGO_DB}, Collection: {MONGO_COLLECTION}")
            
            return True
            
        except (ServerSelectionTimeoutError, ConnectionFailure) as e:
            self._log(f"⚠️  MongoDB connection failed: {e}")
            self._log("   Continuing with file logging only...")
            self.connected = False
            return False
        except Exception as e:
            self._log(f"⚠️  Unexpected MongoDB error: {e}")
            self.connected = False
            return False
    
    def _create_indexes(self):
        """Create indexes for efficient querying"""
        try:
            # Index on session_id for fast session-based queries
            self.collection.create_index([("session_id", ASCENDING)])
            
            # Compound index on session_id and type for fast filtering
            self.collection.create_index([
                ("session_id", ASCENDING),
                ("type", ASCENDING)
            ])
            
            # Index on timestamp for chronological queries
            self.collection.create_index([("timestamp", ASCENDING)])
            
            self._log(f"   📊 Database indexes created/verified")
            
        except Exception as e:
            self._log(f"   ⚠️  Index creation warning: {e}")
    
    def _hash_data(self, data: Any) -> str:
        """Create a deterministic hash of data for comparison"""
        # Convert to JSON string with sorted keys for consistency
        json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return str(hash(json_str))
    
    def _extract_text_hash(self, extracted_texts: List[Dict[str, Any]]) -> str:
        """Create a hash from extracted texts for de-duplication"""
        # Sort and combine all texts into a single string for hashing
        all_texts = []
        for item in extracted_texts:
            if isinstance(item, dict) and 'texts' in item:
                all_texts.extend(item['texts'])
        
        # Sort for consistency
        all_texts.sort()
        combined = '|'.join(all_texts)
        return str(hash(combined))
    
    def _json_objects_hash(self, json_objects: List[Dict[str, Any]]) -> str:
        """Create a hash from JSON objects for de-duplication"""
        # Create a deterministic hash of all JSON objects
        return self._hash_data(json_objects)
    
    def check_duplicate(self, session_id: str, extracted_texts: List[Dict[str, Any]], 
                       json_objects: List[Dict[str, Any]]) -> bool:
        """
        Check if a document with the same session_id + extracted_text + json_objects exists
        
        Returns:
            True if duplicate exists, False if unique
        """
        if not self.connected or self.collection is None:
            return False
        
        try:
            # Create hashes for comparison
            text_hash = self._extract_text_hash(extracted_texts)
            json_hash = self._json_objects_hash(json_objects)
            
            # Query for existing document with same session_id and hashes
            query = {
                "session_id": session_id,
                "type": "api_request",
                "text_hash": text_hash,
                "json_hash": json_hash
            }
            
            existing = self.collection.find_one(query, {"_id": 1})
            
            if existing:
                self._log(f"   🔍 DUPLICATE DETECTED - Skipping insert")
                self._log(f"      Session: {session_id}")
                self._log(f"      Text Hash: {text_hash}")
                self._log(f"      JSON Hash: {json_hash}")
                return True
            
            return False
            
        except Exception as e:
            self._log(f"   ⚠️  Duplicate check failed: {e}")
            # On error, assume not duplicate to avoid data loss
            return False
    
    def insert_request(self, session_id: str, request_num: int, timestamp: datetime.datetime,
                      json_objects: List[Dict[str, Any]], extracted_texts: List[Dict[str, Any]],
                      raw_size_bytes: int, endpoint: str) -> Optional[str]:
        """
        Insert API request data with strict de-duplication
        
        Returns:
            Inserted document ID if successful, None if duplicate or failed
        """
        if not self.connected or self.collection is None:
            return None
        
        try:
            # Check for duplicates first
            if self.check_duplicate(session_id, extracted_texts, json_objects):
                return None  # Duplicate found, skip insert
            
            # Create hashes for this document
            text_hash = self._extract_text_hash(extracted_texts)
            json_hash = self._json_objects_hash(json_objects)
            
            # Prepare document with hashes for future de-duplication
            doc = {
                "session_id": session_id,
                "request_number": request_num,
                "timestamp": timestamp,
                "type": "api_request",
                "endpoint": endpoint,
                "json_objects_count": len(json_objects),
                "json_objects": json_objects,
                "extracted_texts": extracted_texts,
                "raw_size_bytes": raw_size_bytes,
                # Add hashes for efficient de-duplication
                "text_hash": text_hash,
                "json_hash": json_hash
            }
            
            # Insert into MongoDB
            result = self.collection.insert_one(doc)
            self._log(f"   💾 Saved to MongoDB (ID: {result.inserted_id})")
            return str(result.inserted_id)
            
        except Exception as e:
            self._log(f"   ⚠️  MongoDB save failed: {e}")
            return None
    
    def log_session_start(self, session_id: str) -> bool:
        """Log session start event"""
        if not self.connected or self.collection is None:
            return False
        
        try:
            self.collection.insert_one({
                "type": "session_start",
                "session_id": session_id,
                "timestamp": datetime.datetime.now(),
                "source": "cursor_logger"
            })
            return True
        except Exception as e:
            self._log(f"   ⚠️  Failed to log session start: {e}")
            return False
    
    def log_session_end(self, session_id: str, total_requests: int) -> bool:
        """Log session end event"""
        if not self.connected or self.collection is None:
            return False
        
        try:
            self.collection.insert_one({
                "type": "session_end",
                "session_id": session_id,
                "timestamp": datetime.datetime.now(),
                "total_requests": total_requests
            })
            return True
        except Exception as e:
            self._log(f"   ⚠️  Failed to log session end: {e}")
            return False
    
    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """Get statistics for a session"""
        if not self.connected or self.collection is None:
            return {}
        
        try:
            # Count total requests
            total_requests = self.collection.count_documents({
                "session_id": session_id,
                "type": "api_request"
            })
            
            # Count unique requests (by hashes)
            pipeline = [
                {"$match": {"session_id": session_id, "type": "api_request"}},
                {"$group": {
                    "_id": {"text_hash": "$text_hash", "json_hash": "$json_hash"}
                }},
                {"$count": "unique_count"}
            ]
            
            unique_result = list(self.collection.aggregate(pipeline))
            unique_count = unique_result[0]["unique_count"] if unique_result else 0
            
            return {
                "session_id": session_id,
                "total_requests": total_requests,
                "unique_requests": unique_count,
                "duplicates_prevented": total_requests - unique_count
            }
            
        except Exception as e:
            self._log(f"   ⚠️  Failed to get session stats: {e}")
            return {}
    
    def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            self.connected = False
            self._log(f"   🔌 MongoDB connection closed")

