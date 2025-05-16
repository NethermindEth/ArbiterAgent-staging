"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""
import json
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List
import httpx
from datetime import datetime

from app.models.finding_db import Status
from app.config import BACKEND_AGENTS_ENDPOINT, BACKEND_API_KEY, BACKEND_FILES_ENDPOINT, BACKEND_FINDINGS_ENDPOINT, TASK_ID, TESTING, MAX_FINDINGS_PER_SUBMISSION
from app.models.finding_input import FindingInput
from app.database.mongodb_handler import mongodb
from app.core.finding_deduplication import FindingDeduplication
from app.core.final_evaluation import FindingEvaluator

# Initialize FastAPI
app = FastAPI(
    title="Security Findings API",
    description="API for managing security findings and deduplication",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Initialize handlers
deduplicator = FindingDeduplication()
evaluator = FindingEvaluator()

# Add in-memory cache for API data
files_cache = {"task_id": None, "files_content": None}
agents_cache = []

@app.on_event("startup")
async def startup():
    """Connect to MongoDB on startup and fetch initial data."""
    await mongodb.connect()
    print("✅ Connected to MongoDB")
    
    # TODO: Update to use repository download and task details
    # Fetch and cache file contents
    # await fetch_file_contents()
    
    # Fetch and cache agent data
    await fetch_agent_data()

async def fetch_file_contents():
    """Fetch file contents from external API and cache in memory."""
    try:
        if not BACKEND_FILES_ENDPOINT or not BACKEND_API_KEY or not TASK_ID:
            print("BACKEND_FILES_ENDPOINT, BACKEND_API_KEY, or TASK_ID not configured, skipping file contents fetch")
            return
            
        # Construct the full endpoint URL with the task_id
        files_endpoint_with_task_id = f"{BACKEND_FILES_ENDPOINT}/{TASK_ID}"

        async with httpx.AsyncClient() as client:
            headers = {"X-API-Key": BACKEND_API_KEY}
            response = await client.get(files_endpoint_with_task_id, headers=headers)
            
            if response.status_code == 200:
                data = response.json()

                global files_cache
                files_cache = {
                    "task_id": TASK_ID,
                    "files_content": data.get("files_content")
                }
                
                print(f"File contents fetched and cached. Task ID: {files_cache['task_id']}")
            else:
                print(f"Failed to fetch file contents. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error fetching file contents: {str(e)}")

async def fetch_agent_data():
    """Fetch agent data from external API and cache in memory."""
    try:
        if not BACKEND_AGENTS_ENDPOINT or not BACKEND_API_KEY:
            print("BACKEND_AGENTS_ENDPOINT or BACKEND_API_KEY not configured, skipping agent data fetch")
            return
            
        async with httpx.AsyncClient() as client:
            headers = {"X-API-Key": BACKEND_API_KEY}
            response = await client.get(BACKEND_AGENTS_ENDPOINT, headers=headers)
            
            if response.status_code == 200:
                global agents_cache
                agents_cache = response.json()
                print(f"Agent data fetched and cached. Count: {len(agents_cache)}")
            else:
                print(f"Failed to fetch agent data. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error fetching agent data: {str(e)}")

@app.on_event("shutdown")
async def shutdown():
    """Disconnect from MongoDB on shutdown."""
    await mongodb.close()
    print("✅ Disconnected from MongoDB")

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the Security Findings API"}

@app.post("/process_findings", response_model=Dict[str, int])
async def process_findings(input_data: FindingInput, x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Submit security findings for processing.
    Performs deduplication, stores the findings, and automatically evaluates new findings.
    
    Args:
        input_data: Batch of findings with task_id, agent_id and findings list
        x_api_key: API key for authentication
        
    Returns:
        Processing results with statistics and evaluation results
    """
    try:
        # Limit the number of findings per submission
        if len(input_data.findings) > MAX_FINDINGS_PER_SUBMISSION:
            raise HTTPException(
                status_code=400, 
                detail=f"Submission contains too many findings. Maximum allowed: {MAX_FINDINGS_PER_SUBMISSION} findings per submission."
            )
            
        # Verify API key and get agent_id from agents_cache
        agent_id = None
        
        # Check if testing mode is enabled and agents_cache is empty
        if TESTING and not agents_cache:
            agent_id = "test-agent"
            print(f"Testing mode enabled, using test agent_id: {agent_id}")
        elif not agents_cache:
            # If not in testing mode and agents_cache is empty, reject the request
            raise HTTPException(status_code=503, detail="Agent service unavailable. No agents configured.")
        else:
            # Verify API key against known agents
            for agent in agents_cache:
                if agent.get("api_key") == x_api_key:
                    agent_id = agent.get("agent_id")
                    break

        if not agent_id:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # Replace the agent_id from input with the one from the API key
        print(f"Processing findings for task_id: {input_data.task_id}, agent_id: {agent_id}")
        
        # 1. Process findings with deduplication
        dedup_results = await deduplicator.process_findings(agent_id, input_data)
        
        print(f"Deduplication results: {dedup_results}")

        # 1.5. Perform cross-agent comparison for newly added findings
        cross_comparison_results = {}
        if dedup_results['new'] > 0:
            # Get newly added findings
            new_findings = []
            for title in dedup_results.get('new_titles', []):
                finding = await mongodb.get_finding(input_data.task_id, title)
                if finding:
                    new_findings.append(finding)
            
            # Compare with findings from other agents
            if new_findings:
                cross_comparison_results = await evaluator.cross_comparison.compare_with_other_agents(
                    input_data.task_id,
                    agent_id,
                    new_findings
                )
        
        print(f"Cross comparison results: {cross_comparison_results}")

        # 2. Only perform evaluation if new findings were added and not marked as similar_valid
        evaluation_results = {}
        if dedup_results['new'] > 0:
            # Get pending findings (newly added ones that weren't marked as similar_valid)
            pending_findings = await evaluator.get_pending_findings(input_data.task_id)
            
            if pending_findings:
                # Evaluate all pending findings
                evaluation_results = await evaluator.evaluate_all_pending(input_data.task_id)
                
                # Generate summary
                summary_report = await evaluator.generate_summary_report(input_data.task_id)
                evaluation_results["summary"] = summary_report
        
        print(f"Evaluation results: {evaluation_results}")

        # 4. Post results to another endpoint
        try:
            # Get the timestamp of the last sync (will be None if not found)
            last_sync_key = f"last_sync_{input_data.task_id}_{agent_id}"
            last_sync = await mongodb.get_metadata(last_sync_key)
            last_sync_time = last_sync.get("timestamp") if last_sync else None
            
            # Fetch findings created after the last sync time
            if last_sync_time:
                # Get findings created after the last sync
                findings = await mongodb.get_agent_findings_since(
                    input_data.task_id, 
                    agent_id, 
                    last_sync_time
                )
                print(f"Found {len(findings)} new findings since last sync at {last_sync_time}")
            else:
                # If there's no last sync time, get all findings
                findings = await mongodb.get_agent_findings(input_data.task_id, agent_id)
                print(f"No previous sync found. Syncing all {len(findings)} findings")
            
            # Format findings data for the external endpoint
            formatted_findings = []
            summary = { "valid": 0, "already_reported": 0, "disputed": 0 }
            
            # Skip if there are no findings to sync
            if not findings:
                print("No new findings to sync with external endpoint")
                return summary
                
            current_sync_time = datetime.utcnow()
            
            for finding in findings:
                formatted_findings.append({
                    "title": finding.title,
                    "description": finding.description,
                    "severity": finding.severity,
                    "status": finding.status,
                    "file_paths": finding.file_paths,
                    "created_at": finding.created_at.isoformat()
                })

                if finding.status == Status.UNIQUE_VALID or finding.status == Status.SIMILAR_VALID:
                    summary["valid"] += 1
                elif finding.status == Status.ALREADY_REPORTED:
                    summary["already_reported"] += 1
                elif finding.status == Status.DISPUTED:
                    summary["disputed"] += 1

            # Prepare payload for external endpoint
            payload = {
                "task_id": input_data.task_id,
                "agent_id": agent_id,
                "findings": formatted_findings
            }
            
            # Post to external endpoint
            external_endpoint = BACKEND_FINDINGS_ENDPOINT
            if external_endpoint:
                async with httpx.AsyncClient() as client:
                    headers = {"X-API-Key": BACKEND_API_KEY}
                    response = await client.post(external_endpoint, json=payload, headers=headers)
                    print(f"Response: {response.json()}")
                    print(f"Status code: {response.status_code}")
                    
                    # If successful, update the last sync timestamp
                    if response.status_code == 200:
                        await mongodb.set_metadata(last_sync_key, {"timestamp": current_sync_time})
                        print(f"Updated last sync timestamp to {current_sync_time}")
            else:
                # If external API is not configured but in testing mode, still update timestamp
                if TESTING:
                    await mongodb.set_metadata(last_sync_key, {"timestamp": current_sync_time})
                    print(f"TESTING MODE: No external API configured, but updated sync timestamp to {current_sync_time}")
                else:
                    print("EXTERNAL_RESULTS_ENDPOINT not configured, skipping external post")
                
            return summary
        except Exception as post_error:
            print(f"Error posting results to external endpoint: {str(post_error)}")
            raise HTTPException(status_code=500, detail=f"Error posting results to external endpoint: {str(post_error)}")
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error processing findings: {str(e)}")
        print(f"Traceback: {error_trace}")
        
        # Check if this is already an HTTPException so we preserve the status code
        if isinstance(e, HTTPException):
            raise e
        # Otherwise, wrap it in a 500 error
        raise HTTPException(status_code=500, detail=f"Error processing findings: {str(e)}")

@app.get("/tasks/{task_id}/findings", response_model=List[Dict[str, Any]])
async def get_task_findings(task_id: str):
    """
    Get all findings for a task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        List of findings for the task
    """
    try:
        findings = await mongodb.get_task_findings(task_id)
        return [finding.model_dump() for finding in findings]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving findings: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8004, reload=True) 