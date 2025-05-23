"""
MongoDB database handler for security findings.
Handles storage and retrieval of findings in MongoDB.
"""
from typing import List, Dict, Any, Optional
import motor.motor_asyncio
from datetime import datetime
import os
from pydantic import BaseModel

from app.models.finding_input import FindingInput, Finding
from app.models.finding_db import FindingDB
from app.config import config

class MongoDBHandler:
    """
    MongoDB database handler.
    Handles connection and operations on the MongoDB database.
    """
    
    def __init__(self, connection_string: str = None):
        """
        Initialize MongoDB handler with a connection string.
        
        Args:
            connection_string: MongoDB connection string
        """
        self.client = None
        self.db = None
        
        # Automatically select MongoDB URL based on environment
        is_docker = os.path.exists("/.dockerenv")  # Docker environment detection
        default_mongo_url = "mongodb://mongodb:27017" if is_docker else "mongodb://localhost:27017"
        
        self.connection_string = connection_string or config.mongodb_url or default_mongo_url
        self.database_name = "security_findings"
    
    async def connect(self):
        """Connect to MongoDB database."""
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.connection_string)
        self.db = self.client[self.database_name]
    
    async def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
    
    def get_collection_name(self, task_id: str) -> str:
        """
        Get collection name for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Collection name for the task
        """
        return f"findings_{task_id}"
    
    async def create_finding(self, task_id: str, agent_id: str, finding: Finding) -> str:
        """
        Create a new finding in the database.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            finding: The finding to create
            
        Returns:
            Title of the created finding
        """
        # Get collection for this task
        collection = self.get_collection_name(task_id)
        
        # Create FindingDB from Finding
        finding_db = FindingDB(
            **finding.model_dump(),
            agent_id=agent_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Convert to dictionary
        finding_dict = finding_db.model_dump()
        
        # Insert into database
        await self.db[collection].insert_one(finding_dict)
        
        # Return the finding title
        return finding.title
    
    async def create_findings_batch(self, agent_id: str, input_data: FindingInput) -> List[str]:
        """
        Create multiple findings in batch from a FindingInput.
        
        Args:
            agent_id: Agent identifier
            input_data: FindingInput containing task_id and a list of findings
            
        Returns:
            List of created finding titles
        """
        if not input_data.findings:
            return []
            
        # Extract task_id
        task_id = input_data.task_id
        
        # Get collection
        collection = self.get_collection_name(task_id)
        
        # Create FindingDB objects
        finding_dbs = []
        for finding in input_data.findings:
            finding_db = FindingDB(
                **finding.model_dump(),
                agent_id=agent_id,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            finding_dbs.append(finding_db)
        
        # Prepare documents for insertion
        docs = [finding_db.model_dump() for finding_db in finding_dbs]
        
        # Insert all at once
        if docs:
            await self.db[collection].insert_many(docs)
            
        # Return the titles
        return [finding.title for finding in input_data.findings]
    
    async def update_finding(self, task_id: str, title: str, 
                           updated_finding: FindingDB) -> bool:
        """
        Update an existing finding.
        
        Args:
            task_id: Task identifier
            title: Finding title
            updated_finding: Updated finding data
            
        Returns:
            True if update was successful, False otherwise
        """
        collection = self.get_collection_name(task_id)
        
        # Convert to dictionary and update timestamps
        finding_dict = updated_finding.model_dump()
        finding_dict["updated_at"] = datetime.utcnow()
        
        # Update in database
        result = await self.db[collection].update_one(
            {"title": title},
            {"$set": finding_dict}
        )
        
        return result.modified_count > 0
    
    async def get_finding(self, task_id: str, title: str) -> Optional[FindingDB]:
        """
        Get a finding by title.
        
        Args:
            task_id: Task identifier
            title: Finding title
            
        Returns:
            The finding if found, None otherwise
        """
        collection = self.get_collection_name(task_id)
        
        # Query database
        doc = await self.db[collection].find_one({"title": title})
        
        if not doc:
            return None
            
        # Convert to FindingDB
        return FindingDB(**doc)
    
    async def get_task_findings(self, task_id: str) -> List[FindingDB]:
        """
        Get all findings for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            List of findings for the task
        """
        collection = self.get_collection_name(task_id)
        
        # Query database
        cursor = self.db[collection].find({})
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
            
        return findings
    
    async def get_agent_findings(self, task_id: str, agent_id: str) -> List[FindingDB]:
        """
        Get all findings for an agent in a task.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            
        Returns:
            List of findings for the agent in the task
        """
        collection = self.get_collection_name(task_id)
        
        # Query database
        cursor = self.db[collection].find({"agent_id": agent_id})
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
            
        return findings

# Global MongoDB handler instance
mongodb = MongoDBHandler() 