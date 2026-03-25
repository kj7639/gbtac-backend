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
async def analyze_text(request: Request, text, _user=Depends(get_current_user_from_session)):
    key = os.getenv("CONTENT_SAFETY_KEY")
    endpoint = os.getenv("CONTENT_SAFETY_ENDPOINT")

    # Create an Azure AI Content Safety client
    client = ContentSafetyClient(endpoint, AzureKeyCredential(key))

    # Contruct request
    request = AnalyzeTextOptions(text=text)

    # Analyze text
    try:
        response = client.analyze_text(request)
    except HttpResponseError as e:
        print("Analyze text failed.")
        if e.error:
            print(f"Error code: {e.error.code}")
            print(f"Error message: {e.error.message}")
            raise
        print(e)
        raise

    hate_result = next(item for item in response.categories_analysis if item.category == TextCategory.HATE)
    self_harm_result = next(item for item in response.categories_analysis if item.category == TextCategory.SELF_HARM)
    sexual_result = next(item for item in response.categories_analysis if item.category == TextCategory.SEXUAL)
    violence_result = next(item for item in response.categories_analysis if item.category == TextCategory.VIOLENCE)

    return [hate_result, self_harm_result, sexual_result, violence_result]

