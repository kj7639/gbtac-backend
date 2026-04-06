"""
content.py

Provides an endpoint for analyzing user-submitted text using Azure AI Content
Safety. Returns category scores for hate, self-harm, sexual, and violence
content. Protected by authentication and rate limiting.

Author: Kiera Johnson
"""

from routers import *
from helpers.rate_limit import limiter
from fastapi import APIRouter, Request, Depends
import os

from azure.ai.contentsafety import ContentSafetyClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory

from helpers.auth_dependencies import get_current_user_from_session
from dotenv import load_dotenv

router = APIRouter(prefix="/content")

load_dotenv()


@router.get("/safety")
@limiter.limit("10/minute")
async def analyze_text(
    request: Request,
    text: str,
    _user=Depends(get_current_user_from_session)
):
    """
    Analyzes text for harmful content using Azure AI Content Safety.

    Evaluates the provided text across multiple safety categories and returns
    the classification results for hate, self-harm, sexual, and violence content.

    Args:
        request: Incoming request object used for rate limiting.
        text: Text string to analyze.
        _user: Authenticated user dependency (ensures endpoint is protected).

    Returns:
        List of category analysis results for hate, self-harm, sexual, and violence.

    Raises:
        HttpResponseError: If the Azure Content Safety API request fails.
    """
    key = os.getenv("CONTENT_SAFETY_KEY")
    endpoint = os.getenv("CONTENT_SAFETY_ENDPOINT")

    client = ContentSafetyClient(endpoint, AzureKeyCredential(key))

    analyze_request = AnalyzeTextOptions(text=text)

    try:
        response = client.analyze_text(analyze_request)
    except HttpResponseError as e:
        print("Analyze text failed.")
        if e.error:
            print(f"Error code: {e.error.code}")
            print(f"Error message: {e.error.message}")
        raise

    hate_result = next(item for item in response.categories_analysis if item.category == TextCategory.HATE)
    self_harm_result = next(item for item in response.categories_analysis if item.category == TextCategory.SELF_HARM)
    sexual_result = next(item for item in response.categories_analysis if item.category == TextCategory.SEXUAL)
    violence_result = next(item for item in response.categories_analysis if item.category == TextCategory.VIOLENCE)

    return [hate_result, self_harm_result, sexual_result, violence_result]