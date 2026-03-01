"""
Service for recovering incomplete analysis results on application startup.
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionFactory
from app.core.state import InMemoryState
from app.services.feedback_processor import FeedbackProcessorService
from shared_models import AnalysisResult, Feedback, generate_uuid
from shared_models import AnalysisStatus, Sentiment
from app.repositories.feedback_pg import FeedbackPGRepository

logger = structlog.get_logger(__name__)


class AnalysisRecoveryService:
    """Service for recovering incomplete analysis results."""

    def __init__(self):
        self.state = InMemoryState()

    async def recover_incomplete_analyses(self) -> None:
        """
        Recover all incomplete analysis results and continue processing from where they left off.
        """
        logger.info("analysis.recovery.started")

        async with AsyncSessionFactory() as session:
            # Find all incomplete analysis results
            incomplete_analyses = await self._get_incomplete_analyses(session)

            if not incomplete_analyses:
                logger.info("analysis.recovery.no_incomplete_analyses")
                return

            logger.info(f"analysis.recovery.found_incomplete_analyses count={len(incomplete_analyses)}")

            # Process each incomplete analysis
            for analysis in incomplete_analyses:
                try:
                    await self._process_incomplete_analysis(analysis, session)
                except Exception as e:
                    logger.error(
                        "analysis.recovery.error",
                        feedback_id=analysis.feedback_id,
                        status=analysis.status.value,
                        error=str(e),
                    )

        logger.info("analysis.recovery.completed")

    async def _get_incomplete_analyses(self, session: AsyncSession) -> list[AnalysisResult]:
        """
        Get all analysis results that are not completed.

        Args:
            session: Database session

        Returns:
            List of incomplete AnalysisResult objects
        """
        result = await session.execute(
            select(AnalysisResult)
            .where(AnalysisResult.status != AnalysisStatus.COMPLETED)
            .order_by(AnalysisResult.created_at)
        )
        return list(result.scalars().all())

    async def _process_incomplete_analysis(self, analysis: AnalysisResult, session: AsyncSession) -> None:
        """
        Process a single incomplete analysis based on its current status.

        Args:
            analysis: The incomplete AnalysisResult
            session: Database session
        """
        feedback_id = str(analysis.feedback_id)
        logger.info(f"analysis.recovery.processing feedback_id={feedback_id} status={analysis.status.value}")

        # Get feedback data
        feedback = await FeedbackPGRepository(session).get_by_id(feedback_id)
        if not feedback:
            logger.warning(f"analysis.recovery.feedback_not_found feedback_id={feedback_id}")
            return

        # Create processor instance
        processor = FeedbackProcessorService(session)

        # Process based on current status
        if analysis.status == AnalysisStatus.RELEVANT:
            await self._process_relevant_status(processor, feedback, analysis)
        elif analysis.status == AnalysisStatus.ANALYSIS:
            await self._process_analysis_status(processor, feedback, analysis)
        else:
            logger.warning(f"analysis.recovery.unknown_status feedback_id={feedback_id} status={analysis.status.value}")

    async def _process_relevant_status(
        self, processor: FeedbackProcessorService, feedback: Feedback, analysis: AnalysisResult
    ) -> None:
        """
        Process analysis that was interrupted at RELEVANT status.
        """
        logger.info(f"analysis.recovery.processing_relevant feedback_id={feedback.id}")

        # Get comments for this feedback
        comments = await processor._get_feedback_comments(str(feedback.id))
        all_comments = "\n".join([comment.comment for comment in comments])

        if not all_comments.strip():
            logger.warning(f"analysis.recovery.no_comments feedback_id={feedback.id}")
            await processor._create_or_update_analysis_result(
                feedback_id=feedback.id, status=AnalysisStatus.COMPLETED, relevance=False
            )
            await processor._send_analysis_response(feedback, is_relevant=False, state=self.state)
            return

        # Continue with relevance and sentiment analysis
        combined_input = f"Rating: {feedback.rating} stars\nComments: {all_comments}"
        session_id = str(generate_uuid("feedback", str(feedback.id)))

        sentiment = await processor._analyze_with_llm_relevant(
            feedback, combined_input, session_id, feedback.rating, self.state
        )

        if sentiment is None or sentiment in [Sentiment.POSITIVE, Sentiment.NEUTRAL]:
            await processor._create_or_update_analysis_result(
                feedback_id=str(feedback.id), status=AnalysisStatus.COMPLETED
            )
        await processor.process_feedback_session_background(
            feedback, combined_input, session_id, sentiment, self.state, True
        )

    async def _process_analysis_status(
        self, processor: FeedbackProcessorService, feedback: Feedback, analysis: AnalysisResult
    ) -> None:
        """
        Process analysis that was interrupted at ANALYSIS status.
        """
        logger.info(f"analysis.recovery.processing_analysis feedback_id={feedback.id}")

        # Get comments for this feedback
        comments = await processor._get_feedback_comments(str(feedback.id))
        all_comments = "\n".join([comment.comment for comment in comments])
        combined_input = f"Rating: {feedback.rating} stars\nComments: {all_comments}"
        session_id = str(generate_uuid("feedback", str(feedback.id)))

        # Continue with detailed analysis
        analysis_result = await processor._analyze_with_llm_detailed(
            feedback, combined_input, session_id, analysis.sentiment, self.state
        )

        if analysis_result:
            await processor._create_or_update_analysis_result(
                feedback_id=str(feedback.id),
                status=AnalysisStatus.COMPLETED,
                root_causes=analysis_result.root_causes,
                recommendation=analysis_result.recommendation,
            )
