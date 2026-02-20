import json
import logging

import boto3
from ddtrace import tracer

logger = logging.getLogger("parrot.test")


try:
    from ddtrace.llmobs.decorators import agent
except ImportError:
    def agent(**kw):
        def _d(f): return f
        return _d

class TestAgent:
    """Simple test agent to verify Bedrock connection works."""

    def __init__(
        self,
        region: str,
        model_id: str,
        aws_access_key_id: str,
        aws_secret_access_key: str,
        aws_session_token: str = None,
    ):
        self.model_id = model_id
        self.bedrock = boto3.client(
            service_name='bedrock-runtime',
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token
        )
    
    @tracer.wrap(service="parrot", resource="test.test_call")
    @agent(name="test_agent")
    async def test_call(self, message: str) -> str:
        span = tracer.current_span()
        if span:
            span.set_tag("test.model_id", self.model_id)

        try:
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
            
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response['body'].read())
            return response_body['content'][0]['text']

        except Exception as e:
            if span:
                span.set_tag("error", True)
                span.set_tag("error.message", str(e))
            logger.error("Bedrock test call failed: %s", e)
            raise Exception(f"Bedrock call failed: {str(e)}")
