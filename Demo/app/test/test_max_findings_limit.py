"""
Test script for the maximum findings limit feature.
Tests that submissions with more than 20 findings are rejected properly.
"""
import asyncio
import httpx
import traceback
from app.models.finding_input import FindingInput, Finding, Severity
from app.database.mongodb_handler import mongodb
from app.config import TESTING, MAX_FINDINGS_PER_SUBMISSION

# API base URL - this could be moved to a config file too
BASE_URL = "http://localhost:8004"
# API key for authentication
API_KEY = "test-api-key"

async def test_max_findings_limit():
    """Test that submissions with more than MAX_FINDINGS_PER_SUBMISSION findings are rejected with a 400 error."""
    try:
        # Connect to MongoDB for cleanup (optional)
        await mongodb.connect()
        print("✅ Connected to MongoDB")

        task_id = "test-max-findings-limit"
        
        # SCENARIO: Submission with too many findings (should be rejected)
        print(f"\n📋 SCENARIO: Submission with {MAX_FINDINGS_PER_SUBMISSION + 1} findings (should be rejected)")
        
        # Create MAX_FINDINGS_PER_SUBMISSION + 1 findings
        findings_invalid = []
        for i in range(MAX_FINDINGS_PER_SUBMISSION + 1):
            findings_invalid.append(
                Finding(
                    title=f"Test Finding {i+1}",
                    description=f"Test description for finding {i+1}.",
                    file_paths=["contracts/Test.sol"],
                    severity=Severity.MEDIUM
                )
            )
        
        # Create FindingInput
        input_invalid = FindingInput(
            task_id=task_id,
            findings=findings_invalid
        )
        
        # Process findings via API
        print(f"\n📊 Testing submission with {len(findings_invalid)} findings (should be rejected)")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Process findings
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    headers={"X-API-Key": API_KEY},
                    json=input_invalid.model_dump()
                )
                
                # Check response
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 400:
                    print(f"✅ Submission with {len(findings_invalid)} findings was rejected as expected")
                    print(f"Error message: {response.json()['detail']}")
                    assert f"Maximum allowed: {MAX_FINDINGS_PER_SUBMISSION} findings" in response.text, "Expected error message about maximum findings"
                else:
                    print(f"❌ Submission with {len(findings_invalid)} findings was accepted unexpectedly")
                    print(f"Response text: {response.text}")
        except Exception as e:
            print(f"❌ Error occurred: {str(e)}")
            traceback.print_exc()
    
    except Exception as e:
        print(f"❌ Error occurred in test: {str(e)}")
        traceback.print_exc()
    finally:
        await mongodb.close()
        print("\n✅ Test completed")

if __name__ == "__main__":
    asyncio.run(test_max_findings_limit()) 