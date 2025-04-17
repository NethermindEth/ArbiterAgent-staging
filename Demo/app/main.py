"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""
from fastapi import FastAPI, HTTPException, Header
from typing import Dict, Any, List
from app.config import BACKEND_AGENTS_ENDPOINT, BACKEND_API_KEY, BACKEND_FILES_ENDPOINT, BACKEND_FINDINGS_ENDPOINT, TASK_ID
import httpx

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
    
    # Fetch and cache file contents
    await fetch_file_contents()
    
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

@app.post("/process_findings", response_model=Dict[str, Any])
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
        # Verify API key and get agent_id from agents_cache
        agent_id = None
        for agent in agents_cache:
            if agent.get("api_key") == x_api_key:
                agent_id = agent.get("agent_id")
                break

        if not agent_id:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # Replace the agent_id from input with the one from the API key
        print(f"Processing findings for task_id: {input_data.task_id}, agent_id: {agent_id}")
        
        # Use agent_id from API key rather than from the request
        input_data.agent_id = agent_id

        # 1. Process findings with deduplication
        dedup_results = await deduplicator.process_findings(input_data)
        
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
                    input_data.agent_id,
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

        # 3. Combine results
        combined_results = {
            "deduplication": dedup_results,
            "cross_comparison": cross_comparison_results,
            "auto_evaluation": evaluation_results
        }

        # 4. Post results to another endpoint
        try:
            # Fetch all findings for this task to include in the payload
            findings = await mongodb.get_agent_findings(input_data.task_id, input_data.agent_id)
            
            # Format findings data for the external endpoint
            formatted_findings = []
            for finding in findings:
                formatted_findings.append({
                    "title": finding.title,
                    "description": finding.description,
                    "severity": finding.severity,
                    "status": finding.status,
                    "file_path": finding.file_path
                })
            
            # Prepare payload for external endpoint
            payload = {
                "task_id": input_data.task_id,
                "agent_id": input_data.agent_id,
                "findings": formatted_findings
            }
            
            # Post to external endpoint
            external_endpoint = BACKEND_FINDINGS_ENDPOINT
            if external_endpoint:
                async with httpx.AsyncClient() as client:
                    response = await client.post(external_endpoint, json=payload)
                    print(f"Posted results to external endpoint: {response.status_code}")
            else:
                print("EXTERNAL_RESULTS_ENDPOINT not configured, skipping external post")
                
        except Exception as post_error:
            print(f"Error posting results to external endpoint: {str(post_error)}")
            # Continue execution even if posting fails
        
        print(f"Combined results: {combined_results}")
        
        return combined_results
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error processing findings: {str(e)}")
        print(f"Traceback: {error_trace}")
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