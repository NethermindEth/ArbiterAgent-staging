"""
Finding deduplication module for security findings submissions.
Uses LangChain and Claude to compare findings and identify duplicates within the same agent's submissions.
"""
import os
import re
from typing import List, Dict, Any, Optional, Tuple
import asyncio
from dotenv import load_dotenv
from datetime import datetime

from app.core.claude_model import create_similarity_chain
from app.database.mongodb_handler import mongodb
from app.models.finding_input import Finding, FindingInput
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from app.config import config

# Load environment variables
load_dotenv()

# Default similarity threshold
DEFAULT_SIMILARITY_THRESHOLD = 0.8

class FindingDeduplication:
    """
    Handles deduplication of findings submitted by the same agent.
    Identifies findings that have already been reported by the agent.
    Compares title and description using LangChain and Claude.
    """
    
    def __init__(self, mongodb_client=None, similarity_threshold=None):
        """
        Initialize the finding deduplication handler.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
            similarity_threshold: Threshold for considering two findings as duplicates (0.0-1.0)
                                 If None, reads from SIMILARITY_THRESHOLD env var or uses default
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        
        # Get similarity threshold from param, env var, or default
        if similarity_threshold is None:
            similarity_threshold = config.similarity_threshold
        
        # Validate similarity threshold
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0")
        self.similarity_threshold = similarity_threshold
        
        # Initialize similarity chain
        self.similarity_chain = create_similarity_chain()
    
    def get_finding_content(self, finding: Finding) -> str:
        """
        Extract content to be used for similarity comparison from a finding.
        
        Args:
            finding: The finding to extract content from
            
        Returns:
            String containing the relevant fields for comparison
        """
        content = [
            f"Title: {finding.title}",
            f"Description: {finding.description}",
            f"File Paths: {', '.join(finding.file_paths)}",
        ]
        return "\n".join(content)
    
    def _parse_similarity_score(self, response: str) -> float:
        """
        Parse the similarity score from the LLM response.
        
        Args:
            response: Text response from LLM
            
        Returns:
            Similarity score as a float between 0 and 1
        """
        # Look for a floating point number at the end of the response
        # This helps avoid picking up other numbers in the explanation
        match = re.search(r'(\d+\.\d+|\d+)\s*$', response)
        if match:
            score = float(match.group(1))
            # Ensure score is between 0 and 1
            return min(max(score, 0.0), 1.0)
        
        # Look for any decimal number if the end-of-string match fails
        match = re.search(r'(\d+\.\d+|\d+)', response)
        if match:
            score = float(match.group(1))
            # Ensure score is between 0 and 1
            return min(max(score, 0.0), 1.0)
        
        # Default to 0 if no number found
        return 0.0
            
    def _extract_explanation(self, response: str) -> str:
        """
        Extract the explanation part from the LLM response, before the numerical score.
        
        Args:
            response: Text response from LLM
            
        Returns:
            Explanation text
        """
        # Simple approach: split on the first number
        match = re.search(r'(\d+\.\d+|\d+)', response)
        if match:
            explanation = response[:match.start()].strip()
            return explanation
        else:
            return response.strip()
    
    async def compare_findings_with_langchain(self, finding1: Finding, finding2: Finding) -> Tuple[float, str]:
        """
        Compare two findings using LangChain and Claude to determine similarity.
        
        Args:
            finding1: First finding to compare
            finding2: Second finding to compare
            
        Returns:
            Tuple of (similarity score between 0 and 1, explanation text)
        """
        try:
            # Extract content
            content1 = self.get_finding_content(finding1)
            content2 = self.get_finding_content(finding2)
            
            # Run similarity chain
            response_dict = await self.similarity_chain.ainvoke({"finding1": content1, "finding2": content2})
            response = response_dict["similarity"]  # Extract the response string from output_key
            
            # Parse similarity score from response
            similarity_score = self._parse_similarity_score(response)
            
            # Extract explanation
            explanation = self._extract_explanation(response)
            
            return similarity_score, explanation
        except Exception as e:
            # Log the error and return low similarity score
            print(f"Error comparing findings: {str(e)}")
            return 0.0, f"Error during comparison: {str(e)}"
    
    async def _get_agent_findings(self, task_id: str, agent_id: str, exclude_reported: bool = True) -> List[FindingDB]:
        """
        Get all findings submitted by a specific agent for a task.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            exclude_reported: If True, exclude findings already marked as 'already_reported'
            
        Returns:
            List of findings from the specified agent
        """
        try:
            # Get all findings for the task
            all_findings = await self.mongodb.get_task_findings(task_id)
            
            # Filter for the specific agent
            agent_findings = [finding for finding in all_findings if finding.agent_id == agent_id]
            
            # Optionally exclude findings already marked as duplicates
            if exclude_reported:
                return [finding for finding in agent_findings if finding.status != Status.ALREADY_REPORTED]
            
            return agent_findings
        except Exception as e:
            print(f"Error getting agent findings: {str(e)}")
            return []
    
    async def process_findings(self, agent_id: str, input_data: FindingInput) -> Dict[str, Any]:
        """
        Process a batch of new findings, detect duplicates and mark them as already reported.
        Only compares with non-duplicate findings from the same agent.
        
        Args:
            agent_id: Agent identifier
            input_data: FindingInput containing task_id and a list of findings
            
        Returns:
            Statistics about processed findings
        """
        task_id = input_data.task_id
        new_findings = input_data.findings
        
        if not new_findings:
            return {
                "total": 0,
                "duplicates": 0,
                "new": 0,
                "duplicate_titles": [],
                "new_titles": []
            }
        
        try:
            # Get existing findings from same agent, excluding already reported ones
            existing_findings = await self._get_agent_findings(task_id, agent_id, exclude_reported=True)
            
            # Initialize known findings list with existing findings
            known_findings = list(existing_findings)
            
            results = {
                "total": len(new_findings),
                "duplicates": 0,
                "new": 0,
                "duplicate_titles": [],
                "new_titles": []
            }
            
            # List to collect all processed findings for batch processing
            all_processed_findings = []
            
            # Process each finding to determine if it's a duplicate or new
            for finding in new_findings:
                is_duplicate = False
                similarity_explanation = ""
                similar_to = None
                
                # Compare with all known findings (non-duplicate ones from the same agent)
                for known_finding in known_findings:
                    similarity_score, explanation = await self.compare_findings_with_langchain(finding, known_finding)
                    
                    if similarity_score >= self.similarity_threshold:
                        is_duplicate = True
                        similarity_explanation = explanation
                        similar_to = known_finding.title
                        break
                
                if is_duplicate:
                    # Prepare finding with already_reported status
                    duplicate_finding = FindingDB(
                        **finding.model_dump(),
                        agent_id=agent_id,
                        status=Status.ALREADY_REPORTED,
                        evaluation_comment=f"Similar to finding '{similar_to}'. {similarity_explanation}"
                    )
                    
                    # Add to the consolidated findings list
                    all_processed_findings.append(duplicate_finding)
                    
                    results["duplicates"] += 1
                    results["duplicate_titles"].append(finding.title)
                else:
                    # Prepare as new finding
                    new_finding = FindingDB(
                        **finding.model_dump(),
                        agent_id=agent_id
                    )
                    
                    # Add to the consolidated findings list
                    all_processed_findings.append(new_finding)
                    
                    # Add to known findings for subsequent comparisons
                    known_findings.append(new_finding)
                    
                    results["new"] += 1
                    results["new_titles"].append(finding.title)
            
            # Batch create all findings
            if all_processed_findings:
                collection = self.mongodb.get_collection_name(task_id)
                docs = [finding.model_dump() for finding in all_processed_findings]
                
                for doc in docs:
                    doc["created_at"] = datetime.utcnow()
                    doc["updated_at"] = datetime.utcnow()
                    
                await self.mongodb.db[collection].insert_many(docs)
            
            return results
        
        except Exception as e:
            print(f"Error processing findings: {str(e)}")
            return {
                "total": len(new_findings),
                "duplicates": 0,
                "new": 0,
                "duplicate_titles": [],
                "new_titles": [],
                "error": str(e)
            } 