import datetime
from typing import Any, Dict, Optional, Type

import httpx
import structlog
from langfuse import Langfuse
from langfuse.decorators import observe
from openai import AsyncOpenAI

from app.config.settings import settings
from app.models.analysis import RelevantAnalysisModel, ReviewAnalysisModel, SentimentAnalysisModel
from app.services.llm.prompts import SYSTEM_PROMPT_RELEVANT, SYSTEM_PROMPT_SENTIMENT, SYSTEM_PROMPT_ZONE

logger = structlog.get_logger(__name__)


class LLMAnalysisService:
    def __init__(self, api_key: str = None):
        self.settings = settings
        self.api_key = api_key or self.settings.LLM_API_KEY

        self.client = self._get_async_client()

        # Initialize Langfuse with error handling
        self.langfuse_client = None
        try:
            self.langfuse_client = Langfuse(
                secret_key=self.settings.LANGFUSE_SECRET_KEY,
                public_key=self.settings.LANGFUSE_PUBLIC_KEY,
                host=self.settings.LANGFUSE_HOST,
            )
            logger.info("Langfuse client initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize Langfuse client", error=str(e))

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Calculate cost for LLM usage

        Args:
            model: Model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Calculated cost in USD
        """

        pricing = self.settings.MODEL_PRICE[model]
        input_cost = (input_tokens / 1000) * pricing["input"]
        output_cost = (output_tokens / 1000) * pricing["output"]
        total_cost = input_cost + output_cost
        return total_cost

    def _get_async_client(self):
        """Get async OpenAI client for LLM calls with optimized HTTP client."""
        # Create optimized HTTP client with connection pooling
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,  # Keep connections alive
                max_connections=100,  # Max concurrent connections
                keepalive_expiry=30.0,  # Keep connections for 30 seconds
            ),
            timeout=httpx.Timeout(
                connect=5.0,  # Connection timeout
                read=30.0,  # Read timeout
                write=10.0,  # Write timeout
                pool=5.0,  # Pool timeout
            ),
        )

        return AsyncOpenAI(api_key=self.api_key, base_url=self.settings.LLM_API_URL, http_client=http_client)

    def _prepare_input_params(
        self, input_data: Dict[str, Any], system_prompt: str, user_input: str
    ) -> tuple[dict, dict]:
        """Prepare input parameters for LLM model calls."""
        model_params = {
            "model": self.settings.LLM_MODEL_NAME,
            "temperature": self.settings.LLM_TEMPERATURE,
            "max_tokens": self.settings.LLM_MAX_TOKENS,
        }

        enhanced_input_data = {
            **input_data,
            "model_parameters": model_params,
            "system_prompt": system_prompt,
            "user_input": user_input,
        }
        return model_params, enhanced_input_data

    async def _call_llm_model(
        self,
        system_prompt: str,
        user_input: str,
        response_format: Type[Any],
        span_name: str,
        input_data: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> Any:
        """Common function for all LLM model calls with optimizations."""
        start_datetime = datetime.datetime.now()

        # First, call LLM to get the response
        response = await self._make_llm_request(system_prompt, user_input, response_format)
        parsed_result = response.choices[0].message.parsed

        end_datetime = datetime.datetime.now()

        # Calculate cost for tracking
        cost = None
        if response.usage:
            try:
                cost = self.calculate_cost(
                    model=self.settings.LLM_MODEL_NAME,
                    input_tokens=response.usage.prompt_tokens,
                    output_tokens=response.usage.completion_tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to calculate cost for {span_name}", error=str(e))

        # Then, try to log to Langfuse if available
        if self.langfuse_client is not None:
            model_params, enhanced_input_data = self._prepare_input_params(input_data, system_prompt, user_input)

            model_input_price = self.settings.MODEL_PRICE[self.settings.LLM_MODEL_NAME].get("input", 0)
            model_output_price = self.settings.MODEL_PRICE[self.settings.LLM_MODEL_NAME].get("output", 0)
            try:
                trace = None
                if session_id:
                    trace = self.langfuse_client.trace(
                        name=f"{span_name}_trace",
                        session_id=session_id,
                        user_id="system",
                        tags=["llm_analysis", span_name],
                        start_time=start_datetime,
                        metadata={
                            "model": self.settings.LLM_MODEL_NAME,
                            "input_tokens": response.usage.prompt_tokens if response.usage else None,
                            "output_tokens": response.usage.completion_tokens if response.usage else None,
                            "total_tokens": response.usage.total_tokens if response.usage else None,
                            "cost": cost,
                            "span_name": span_name,
                            "analysis_type": span_name,
                        },
                    )

                generation = self.langfuse_client.generation(
                    name=span_name,
                    model=self.settings.LLM_MODEL_NAME,
                    input=enhanced_input_data,
                    usage={
                        "input": response.usage.prompt_tokens,
                        "output": response.usage.completion_tokens,
                    },
                    total_cost=cost,
                    start_time=start_datetime,
                    end_time=end_datetime,
                    metadata={
                        "model": self.settings.LLM_MODEL_NAME,
                        "cost": cost,
                        "input_tokens": response.usage.prompt_tokens if response.usage else None,
                        "output_tokens": response.usage.completion_tokens if response.usage else None,
                        "total_tokens": response.usage.total_tokens if response.usage else None,
                        "span_name": span_name,
                        "analysis_type": span_name,
                        "cost_breakdown": (
                            {
                                "input_cost": (
                                    (response.usage.prompt_tokens / 1000) * model_input_price if response.usage else 0
                                ),
                                "output_cost": (
                                    (response.usage.completion_tokens / 1000) * model_output_price
                                    if response.usage
                                    else 0
                                ),
                                "total_cost": cost,
                            }
                            if cost is not None
                            else None
                        ),
                    },
                    model_parameters=model_params,
                    trace_id=trace.id if trace else None,
                )

                response_data = {
                    "result": parsed_result,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                    "model": response.model,
                }

                generation.update(output=response_data, usage_details=response.usage)
                generation.end()

                if trace:
                    trace.update(
                        output={"analysis_completed": True},
                        end_time=end_datetime,
                        metadata={
                            "span_name": span_name,
                            "cost": cost,
                            "model": self.settings.LLM_MODEL_NAME,
                            "input_tokens": response.usage.prompt_tokens,
                            "output_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens,
                        },
                    )

            except Exception as e:
                logger.warning("Langfuse logging failed", error=str(e))

        return parsed_result

    async def _make_llm_request(
        self,
        system_prompt: str,
        user_input: str,
        response_format: Type[Any],
    ):
        """Make LLM request - common method to avoid code duplication."""
        return await self.client.beta.chat.completions.parse(
            model=self.settings.LLM_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            temperature=self.settings.LLM_TEMPERATURE,
            max_tokens=self.settings.LLM_MAX_TOKENS,
            response_format=response_format,
        )

    @observe(name="sentiment_analysis", as_type="generation")
    async def detect_sentiment(
        self, user_input: str, rating: Optional[int] = None, session_id: Optional[str] = None
    ) -> str:
        """Detect sentiment from user input (which already includes rating)."""
        result = await self._call_llm_model(
            system_prompt=SYSTEM_PROMPT_SENTIMENT,
            user_input=user_input,
            response_format=SentimentAnalysisModel,
            span_name="sentiment_analysis",
            input_data={"user_input": user_input, "rating": rating},
            session_id=session_id,
        )
        return result.sentiment.value

    @observe(name="relevant_analysis", as_type="generation")
    async def check_relevant_review(self, user_input: str, session_id: Optional[str] = None) -> bool:
        """Check if the review is relevant to hotel zones."""
        result = await self._call_llm_model(
            system_prompt=SYSTEM_PROMPT_RELEVANT,
            user_input=user_input,
            response_format=RelevantAnalysisModel,
            span_name="relevant_analysis",
            input_data={"user_input": user_input},
            session_id=session_id,
        )
        return result.relevant

    @observe(name="review_analysis", as_type="generation")
    async def analyze_review(
        self, user_input: str, category: str, criteria: str, session_id: Optional[str] = None
    ) -> tuple[list[str], str]:
        """Analyze review and extract tags and recommendations."""

        # Format the system prompt with category and criteria
        system_prompt = SYSTEM_PROMPT_ZONE.format(category=category, criteria=criteria)

        result = await self._call_llm_model(
            system_prompt=system_prompt,
            user_input=user_input,
            response_format=ReviewAnalysisModel,
            span_name="review_analysis",
            input_data={"user_input": user_input, "category": category},
            session_id=session_id,
        )
        return result.tags, result.recommendation

    async def flush_langfuse(self) -> None:
        """Flush Langfuse data with error handling."""
        try:
            if self.langfuse_client:
                self.langfuse_client.flush()
                logger.debug("Langfuse data flushed successfully")
        except Exception as e:
            logger.warning("Failed to flush Langfuse", error=str(e))
