import boto3
import json
from typing import Dict

class TestAgent:
    """Simple test agent to verify Bedrock connection works"""
    
    def __init__(self, region: str, model_id: str, aws_access_key_id: str, aws_secret_access_key: str, aws_session_token: str = None):
        self.model_id = model_id
        self.bedrock = boto3.client(
            service_name='bedrock-runtime',
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token
        )
    
    async def test_call(self, message: str) -> str:
        """Test basic Bedrock call with Claude"""
        try:
            # Prepare request for Claude via Bedrock
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            }
            
            # Call Bedrock
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body)
            )
            
            # Parse response
            response_body = json.loads(response['body'].read())
            return response_body['content'][0]['text']
            
        except Exception as e:
            raise Exception(f"Bedrock call failed: {str(e)}")
