"""
Test the sync mechanism for syncing findings with external endpoints.
"""
import asyncio
import json
import os
import sys
import httpx
import traceback
import re
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.models.finding_input import FindingInput, Finding
from app.models.finding_db import Severity
from app.database.mongodb_handler import mongodb
from app.config import TESTING, MAX_FINDINGS_PER_SUBMISSION, BACKEND_API_KEY

# Configuration
BASE_URL = "http://localhost:8004"
API_KEY = "test-api-key"  # Use a valid API key or the test-agent key

async def test_sync_mechanism():
    """
    Test the sync mechanism
    
    Scenarios:
    1. First submission: All findings should be synced
    2. Second submission: Only newly created findings should be synced
    3. Empty submission: Nothing should be synced
    """
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")
        
        task_id = "test-sync-mechanism"
        print(f"\n🔍 Starting sync mechanism test with task_id: {task_id}")
        
        # Clean test data
        collection = mongodb.get_collection_name(task_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")
            
        # Clean metadata
        metadata_collection = "metadata"
        if metadata_collection in await mongodb.db.list_collection_names():
            metadata_key = f"last_sync_{task_id}_test-agent"
            delete_result = await mongodb.db[metadata_collection].delete_one({"key": metadata_key})
            if delete_result.deleted_count > 0:
                print(f"🧹 Deleted metadata record for {metadata_key}")
        
        # Scenario 1: First submission - all findings should be synced
        print("\n📋 SCENARIO 1: First submission - all findings should be synced")
        
        # Create test findings
        findings_batch1 = [
            Finding(
                title="First Finding",
                description="This is the first finding to test sync.",
                severity=Severity.HIGH,
                file_paths=["contracts/Test.sol"]
            ),
            Finding(
                title="Second Finding",
                description="This is the second finding to test sync.",
                severity=Severity.MEDIUM,
                file_paths=["contracts/Test.sol"]
            )
        ]
        
        # Create submission
        input_batch1 = FindingInput(
            task_id=task_id,
            findings=findings_batch1
        )
        
        # Process findings
        print("\n📊 Processing first batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                print(f"Sending POST request to {BASE_URL}/process_findings")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    headers={"X-API-Key": API_KEY},
                    json=input_batch1.model_dump()
                )
                
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ First batch processed successfully")
                    print(f"Response: {result}")
                    
                    # Check metadata to verify the last sync timestamp was updated
                    last_sync_key = f"last_sync_{task_id}_test-agent"
                    last_sync = await mongodb.get_metadata(last_sync_key)
                    
                    if last_sync and "timestamp" in last_sync:
                        sync_time = last_sync["timestamp"]
                        print(f"✅ Found sync timestamp: {sync_time}")
                    else:
                        print(f"❌ Sync timestamp not found")
                        sync_time = None
                else:
                    print(f"❌ Failed to process first batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Error processing first batch: {str(e)}")
            traceback.print_exc()
            return
        
        # Wait to ensure timestamp difference
        print("\n⏱️ Waiting to ensure timestamp difference between batches...")
        await asyncio.sleep(1)  
        
        # Scenario 2: Second submission - only new findings should be synced
        print("\n📋 SCENARIO 2: Second submission - only new findings should be synced")
        
        # Create second batch of findings
        findings_batch2 = [
            Finding(
                title="Third Finding",
                description="This is a new finding that should be synced.",
                severity=Severity.HIGH,
                file_paths=["contracts/Different.sol"]
            )
        ]
        
        # Create submission
        input_batch2 = FindingInput(
            task_id=task_id,
            findings=findings_batch2
        )
        
        # Process findings
        print("\n📊 Processing second batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                print(f"Sending POST request to {BASE_URL}/process_findings")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    headers={"X-API-Key": API_KEY},
                    json=input_batch2.model_dump()
                )
                
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Second batch processed successfully")
                    print(f"Response: {result}")
                    
                    # Check metadata to verify the sync timestamp was updated
                    last_sync_key = f"last_sync_{task_id}_test-agent"
                    new_sync = await mongodb.get_metadata(last_sync_key)
                    
                    if new_sync and "timestamp" in new_sync:
                        new_sync_time = new_sync["timestamp"]
                        print(f"✅ Sync timestamp updated: {new_sync_time}")
                        
                        # Verify the timestamp was updated (should be newer)
                        if sync_time and new_sync_time > sync_time:
                            print(f"✅ Timestamp correctly updated")
                        else:
                            print(f"❓ Timestamp may not have been updated correctly")
                    else:
                        print(f"❌ Sync timestamp not found after second batch")
                else:
                    print(f"❌ Failed to process second batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Error processing second batch: {str(e)}")
            traceback.print_exc()
            return
            
        # Scenario 3: Empty submission - nothing should be synced
        print("\n📋 SCENARIO 3: Empty submission - nothing should be synced")
        
        # Create an empty batch of findings
        findings_batch3 = []
        
        # Create submission
        input_batch3 = FindingInput(
            task_id=task_id,
            findings=findings_batch3
        )
        
        # Process findings
        print("\n📊 Processing empty batch")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                print(f"Sending POST request to {BASE_URL}/process_findings")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    headers={"X-API-Key": API_KEY},
                    json=input_batch3.model_dump()
                )
                
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Empty batch processed successfully")
                    print(f"Response: {result}")
                    
                    # Get the latest timestamp
                    last_sync_key = f"last_sync_{task_id}_test-agent"
                    latest_sync = await mongodb.get_metadata(last_sync_key)
                    
                    if latest_sync and "timestamp" in latest_sync:
                        last_time = latest_sync["timestamp"]
                        print(f"✅ Final sync timestamp: {last_time}")
                        
                        # Verify this timestamp is the same as previous (should not update)
                        if new_sync and last_time == new_sync["timestamp"]:
                            print(f"✅ Empty batch did not update timestamp (expected behavior)")
                        else:
                            print(f"❓ Timestamp changed after empty batch")
                    else:
                        print(f"❌ Sync timestamp not found after empty batch")
                else:
                    print(f"❌ Failed to process empty batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Error processing empty batch: {str(e)}")
            traceback.print_exc()
            return
        
        # Output final sync status
        print("\n📊 Final sync status")
        
        # Get the last sync metadata
        last_sync_key = f"last_sync_{task_id}_test-agent"
        final_sync = await mongodb.get_metadata(last_sync_key)
        
        if final_sync:
            print(f"✅ Final sync metadata record:")
            for key, value in final_sync.items():
                print(f"  {key}: {value}")
        else:
            print(f"❌ Final sync metadata not found")
            
        # Get total number of records in database
        findings = await mongodb.get_task_findings(task_id)
        print(f"📊 Total findings in database: {len(findings)}")
                
        print("\n✅ Sync mechanism test completed")
    except Exception as e:
        print(f"❌ Test error: {str(e)}")
        traceback.print_exc()
    finally:
        # Disconnect from MongoDB
        await mongodb.close()
        print("\n📡 Disconnected from MongoDB")

# Run the test
if __name__ == "__main__":
    asyncio.run(test_sync_mechanism()) 