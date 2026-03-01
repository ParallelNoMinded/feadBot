"""
Service for managing LLM API key pools and load balancing.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

from app.config.settings import settings
from app.models.constants import Priority
from app.services.llm.llm_analysis import LLMAnalysisService

logger = structlog.get_logger(__name__)


@dataclass
class TaskWrapper:
    """Wrapper for tasks with priority and metadata"""

    priority: int
    task_id: str
    task_type: str  # "relevance", "sentiment", "analysis"
    func: Callable
    args: Tuple
    kwargs: Dict[str, Any]
    timestamp: float
    future: Optional[asyncio.Future] = None

    def __lt__(self, other):
        """Priority queue comparison (priority first, then FIFO)"""
        if self.priority == other.priority:
            return self.timestamp < other.timestamp
        return self.priority < other.priority


class LLMPoolService:
    """Service for managing multiple LLM API keys with load balancing."""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMPoolService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.settings = settings
            self._relevance_services: list[LLMAnalysisService] = []
            self._sentiment_services: list[LLMAnalysisService] = []
            self._relevance_index = 0
            self._sentiment_index = 0
            self._lock = asyncio.Lock()

            # Track active requests per service
            self._relevance_active_requests: dict[str, int] = {}
            self._sentiment_active_requests: dict[str, int] = {}
            self._max_concurrent_requests = 10  # Max concurrent requests per API key

            # Unified priority queue for all tasks
            self._unified_waiting_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
            self._max_wait_time = 30.0  # Max wait time in seconds

            # Semaphores for rate limiting (will be initialized after services)
            self._relevance_semaphore = None
            self._sentiment_semaphore = None

            # Task counter for unique IDs
            self._task_counter = 0

            # Worker tasks for processing queues
            self._unified_worker_task = None
            self._running = False

            self._initialize_services()
            LLMPoolService._initialized = True

    def _initialize_services(self):
        """Initialize LLM services with available API keys."""
        logger.info("Initializing LLM pool services...")

        # Primary API key for relevance analysis
        if self.settings.LLM_API_KEY:
            service = LLMAnalysisService(api_key=self.settings.LLM_API_KEY)
            self._relevance_services.append(service)
            self._relevance_active_requests[self.settings.LLM_API_KEY] = 0
            logger.info("Added relevance analysis service with primary API key")

        # Secondary API key for sentiment analysis (or fallback to primary)
        if self.settings.LLM_API_KEY_2:
            service = LLMAnalysisService(api_key=self.settings.LLM_API_KEY_2)
            self._sentiment_services.append(service)
            self._sentiment_active_requests[self.settings.LLM_API_KEY_2] = 0
            logger.info("Added sentiment analysis service with secondary API key")

        # Add second key to relevance pool
        service = LLMAnalysisService(api_key=self.settings.LLM_API_KEY_2)
        self._relevance_services.append(service)
        self._relevance_active_requests[self.settings.LLM_API_KEY_2] = 0

        # Add first key to sentiment pool
        service = LLMAnalysisService(api_key=self.settings.LLM_API_KEY)
        self._sentiment_services.append(service)
        self._sentiment_active_requests[self.settings.LLM_API_KEY] = 0

        logger.info("Added second API key to both pools for load balancing")

        logger.info(
            f"LLM pool initialized: {len(self._relevance_services)} relevance services, {len(self._sentiment_services)} sentiment services"
        )

        # Initialize semaphores after services are created
        self._relevance_semaphore = asyncio.Semaphore(self._max_concurrent_requests * 2)
        self._sentiment_semaphore = asyncio.Semaphore(self._max_concurrent_requests * 2)

        # Pre-warm connections for better performance
        asyncio.create_task(self._pre_warm_connections())

        # Start unified worker
        self._start_workers()

    async def _pre_warm_connections(self):
        """Pre-warm HTTP connections for better performance."""

        # Warm up connections for all services
        warm_tasks = []
        for service in self._relevance_services + self._sentiment_services:
            # Create a simple health check task
            warm_tasks.append(self._warm_service_connection(service))
        try:
            # Wait for all connections to be warmed up
            await asyncio.gather(*warm_tasks, return_exceptions=True)
            logger.info("LLM pool connections pre-warmed successfully")
        except Exception as e:
            logger.warning(f"Failed to pre-warm connections: {e}")

    async def _warm_service_connection(self, service: LLMAnalysisService):
        """Warm up a single service connection."""
        try:
            # Simple health check to establish connection
            await service.client.models.list()
        except Exception as e:
            logger.warning(f"Failed to warm up service connection: {e}")

    def _start_workers(self):
        """Start background worker for processing unified priority queue."""
        if not self._running:
            self._running = True
            self._unified_worker_task = asyncio.create_task(self._unified_worker())
            logger.info("Started unified priority queue worker")

    def _stop_workers(self):
        """Stop background worker."""
        if self._running:
            self._running = False
            if self._unified_worker_task:
                self._unified_worker_task.cancel()
            logger.info("Stopped unified priority queue worker")

    async def _unified_worker(self):
        """Unified worker that processes all tasks with strict priority ordering."""
        logger.info("Unified worker started with strict priority processing")

        while self._running:
            try:
                # Process HIGH priority tasks first - ALL of them
                await self._process_high_priority_tasks()

                # Only process MEDIUM priority tasks if no HIGH priority tasks are waiting
                # This ensures strict priority ordering
                await self._process_medium_priority_tasks()

                # Brief pause to prevent busy waiting
                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                logger.info("Unified worker cancelled")
                break
            except Exception as e:
                logger.error(f"Unified worker error: {e}")
                await asyncio.sleep(0.1)  # Brief pause before retrying

    async def _process_high_priority_tasks(self):
        """Process all HIGH priority tasks in the queue."""
        high_tasks_processed = 0
        while not self._unified_waiting_queue.empty():
            try:
                # Get task from queue
                task_wrapper = await self._unified_waiting_queue.get()

                # Check if it's HIGH priority
                if task_wrapper.priority == Priority.HIGH:
                    logger.info(f"Processing HIGH priority task: {task_wrapper.task_id} ({task_wrapper.task_type})")
                    await self._process_single_task(task_wrapper)
                    high_tasks_processed += 1
                else:
                    # Put it back in queue (it's MEDIUM priority)
                    await self._unified_waiting_queue.put(task_wrapper)
                    logger.debug(
                        f"Found MEDIUM priority task, stopping HIGH processing. Processed {high_tasks_processed} HIGH tasks"
                    )
                    break  # Stop processing HIGH tasks
            except Exception as e:
                logger.error(f"Error processing HIGH priority task: {e}")
                break

        if high_tasks_processed > 0:
            logger.debug(f"Processed {high_tasks_processed} HIGH priority tasks")

    async def _process_medium_priority_tasks(self):
        """Process MEDIUM priority tasks only if no HIGH priority tasks are waiting."""
        medium_tasks_processed = 0
        # Process all MEDIUM priority tasks in the queue
        while not self._unified_waiting_queue.empty():
            try:
                # Get task from queue
                task_wrapper = await self._unified_waiting_queue.get()

                if task_wrapper.priority == Priority.HIGH:
                    # Put it back and stop processing MEDIUM tasks
                    await self._unified_waiting_queue.put(task_wrapper)
                    logger.debug("Found HIGH priority task, stopping MEDIUM processing")
                    break
                else:
                    # It's MEDIUM priority, process it
                    logger.info(f"Processing MEDIUM priority task: {task_wrapper.task_id} ({task_wrapper.task_type})")
                    await self._process_single_task(task_wrapper)
                    medium_tasks_processed += 1
            except Exception as e:
                logger.error(f"Error processing MEDIUM priority task: {e}")
                break

        if medium_tasks_processed > 0:
            logger.debug(f"Processed {medium_tasks_processed} MEDIUM priority tasks")

    async def _process_single_task(self, task_wrapper: TaskWrapper):
        """Process a single task and assign service."""
        try:
            # Determine which service to use based on task type
            service = None
            if task_wrapper.task_type == "relevance":
                service = await self._try_get_relevance_service()
            elif task_wrapper.task_type == "sentiment":
                service = await self._try_get_sentiment_service()
            elif task_wrapper.task_type == "analysis":
                service = await self._try_get_relevance_service()  # Analysis uses relevance service

            if service and not task_wrapper.future.done():
                # Set the service as result for the future
                task_wrapper.future.set_result(service)
                logger.debug(f"Service assigned to {task_wrapper.task_type} task {task_wrapper.task_id}")
            else:
                # No service available, put task back in queue
                await self._unified_waiting_queue.put(task_wrapper)
                logger.debug(f"No service available for {task_wrapper.task_type} task {task_wrapper.task_id}, requeued")

        except Exception as e:
            logger.error(f"Error processing {task_wrapper.task_type} task {task_wrapper.task_id}: {e}")
            if not task_wrapper.future.done():
                task_wrapper.future.set_exception(e)

    async def get_relevance_service(self) -> Optional[LLMAnalysisService]:
        """Get next available service for relevance analysis with smart load balancing and queuing."""
        return await self.get_relevance_service_with_priority(Priority.HIGH)

    async def get_relevance_service_with_priority(self, priority: Priority) -> Optional[LLMAnalysisService]:
        """Get next available service for relevance analysis with specified priority."""
        if not self._relevance_services:
            logger.error("No relevance analysis services available")
            return None

        # Try to get service immediately
        service = await self._try_get_relevance_service()
        if service:
            return service

        # If no service available, wait in queue with priority
        logger.info(f"All relevance services busy, waiting in queue with priority {priority.name}...")
        try:
            # Wait for service to become available
            service = await asyncio.wait_for(self._wait_for_relevance_service(priority), timeout=self._max_wait_time)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for relevance service")
            return None

        return service

    async def _try_get_relevance_service(self) -> Optional[LLMAnalysisService]:
        """Try to get a relevance service immediately without waiting."""
        # Acquire semaphore to limit concurrent requests
        if self._relevance_semaphore is None:
            logger.warning("Relevance semaphore not initialized")
            return None

        logger.debug(f"Acquiring relevance semaphore, available: {self._relevance_semaphore._value}")
        await self._relevance_semaphore.acquire()

        async with self._lock:
            # Find service with least active requests
            best_service = None
            min_requests = float("inf")

            for service in self._relevance_services:
                api_key = service.api_key
                active_requests = self._relevance_active_requests.get(api_key, 0)

                if active_requests < self._max_concurrent_requests and active_requests < min_requests:
                    best_service = service
                    min_requests = active_requests

            if best_service:
                # Increment active requests counter
                self._relevance_active_requests[best_service.api_key] += 1
                logger.debug(f"Selected relevance service with {min_requests} active requests")
                return best_service

            # Release semaphore if no service available
            self._relevance_semaphore.release()
            logger.debug("No available relevance service, semaphore released")
            return None

    async def _wait_for_relevance_service(self, priority: Priority = Priority.HIGH) -> Optional[LLMAnalysisService]:
        """Wait for a relevance service to become available with priority."""
        # Create a future to be resolved when service becomes available
        future = asyncio.Future()

        # Create task wrapper with priority
        task_id = f"relevance-{self._task_counter}"
        self._task_counter += 1

        task_wrapper = TaskWrapper(
            priority=priority,
            task_id=task_id,
            task_type="relevance",
            func=lambda: None,  # Placeholder function
            args=(),
            kwargs={},
            timestamp=time.time(),
            future=future,
        )

        logger.info(f"Queuing relevance task {task_id} with priority {priority.name}")

        # Put the task wrapper in the unified priority queue
        await self._unified_waiting_queue.put(task_wrapper)

        # Wait for the future to be resolved
        logger.debug(f"Waiting for relevance service for task {task_id}")
        return await future

    async def get_sentiment_service(self) -> Optional[LLMAnalysisService]:
        """Get next available service for sentiment analysis with smart load balancing and queuing."""
        return await self.get_sentiment_service_with_priority(Priority.HIGH)

    async def get_sentiment_service_with_priority(self, priority: Priority) -> Optional[LLMAnalysisService]:
        """Get next available service for sentiment analysis with specified priority."""
        if not self._sentiment_services:
            logger.error("No sentiment analysis services available")
            return None

        # Try to get service immediately
        service = await self._try_get_sentiment_service()
        if service:
            return service

        # If no service available, wait in queue with priority
        logger.info(f"All sentiment services busy, waiting in queue with priority {priority.name}...")
        try:
            # Wait for service to become available
            service = await asyncio.wait_for(self._wait_for_sentiment_service(priority), timeout=self._max_wait_time)
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for sentiment service")
            return None

        return service

    async def _try_get_sentiment_service(self) -> Optional[LLMAnalysisService]:
        """Try to get a sentiment service immediately without waiting."""
        # Acquire semaphore to limit concurrent requests
        if self._sentiment_semaphore is None:
            logger.warning("Sentiment semaphore not initialized")
            return None

        logger.debug(f"Acquiring sentiment semaphore, available: {self._sentiment_semaphore._value}")
        await self._sentiment_semaphore.acquire()

        async with self._lock:
            # Find service with least active requests
            best_service = None
            min_requests = float("inf")

            for service in self._sentiment_services:
                api_key = service.api_key
                active_requests = self._sentiment_active_requests.get(api_key, 0)

                if active_requests < self._max_concurrent_requests and active_requests < min_requests:
                    best_service = service
                    min_requests = active_requests

            if best_service:
                # Increment active requests counter
                self._sentiment_active_requests[best_service.api_key] += 1
                logger.debug(f"Selected sentiment service with {min_requests} active requests")
                return best_service

            # Release semaphore if no service available
            self._sentiment_semaphore.release()
            logger.debug("No available sentiment service, semaphore released")
            return None

    async def _wait_for_sentiment_service(self, priority: Priority = Priority.HIGH) -> Optional[LLMAnalysisService]:
        """Wait for a sentiment service to become available with priority."""
        # Create a future to be resolved when service becomes available
        future = asyncio.Future()

        # Create task wrapper with priority
        task_id = f"sentiment-{self._task_counter}"
        self._task_counter += 1

        task_wrapper = TaskWrapper(
            priority=priority,
            task_id=task_id,
            task_type="sentiment",
            func=lambda: None,  # Placeholder function
            args=(),
            kwargs={},
            timestamp=time.time(),
            future=future,
        )

        logger.info(f"Queuing sentiment task {task_id} with priority {priority.name}")

        # Put the task wrapper in the unified priority queue
        await self._unified_waiting_queue.put(task_wrapper)

        # Wait for the future to be resolved
        logger.debug(f"Waiting for sentiment service for task {task_id}")
        return await future

    def _release_relevance_service(self, service: LLMAnalysisService):
        """Release a relevance service after request completion."""

        async def _release():
            async with self._lock:
                api_key = service.api_key
                if api_key in self._relevance_active_requests:
                    self._relevance_active_requests[api_key] = max(0, self._relevance_active_requests[api_key] - 1)
                    logger.info(
                        f"Released relevance service, {self._relevance_active_requests[api_key]} active requests remaining"
                    )

            # Release semaphore
            if self._relevance_semaphore is not None:
                self._relevance_semaphore.release()

        # Run in background to avoid blocking
        asyncio.create_task(_release())

    def _release_sentiment_service(self, service: LLMAnalysisService):
        """Release a sentiment service after request completion."""

        async def _release():
            async with self._lock:
                api_key = service.api_key
                if api_key in self._sentiment_active_requests:
                    self._sentiment_active_requests[api_key] = max(0, self._sentiment_active_requests[api_key] - 1)
                    logger.info(
                        f"Released sentiment service, {self._sentiment_active_requests[api_key]} active requests remaining"
                    )

            # Release semaphore
            if self._sentiment_semaphore is not None:
                self._sentiment_semaphore.release()

        # Run in background to avoid blocking
        asyncio.create_task(_release())

    async def check_relevant_review(
        self, user_input: str, session_id: Optional[str] = None
    ) -> tuple[bool, Optional[LLMAnalysisService]]:
        """Check if review is relevant using load-balanced service with optimizations."""
        service = await self.get_relevance_service()
        if not service:
            logger.error("No relevance service available")
            return False, None

        try:
            # Optimized call with pre-warmed connection
            result = await service.check_relevant_review(user_input=user_input, session_id=session_id)
            # Release service after successful completion
            self._release_relevance_service(service)
            return result, service
        except Exception as e:
            logger.error(f"Relevance analysis failed: {str(e)}", exc_info=True)

            # Release failed service
            self._release_relevance_service(service)

            # Try with next service if available
            if len(self._relevance_services) > 1:
                service = await self.get_relevance_service()
                if service:
                    try:
                        result = await service.check_relevant_review(user_input=user_input, session_id=session_id)
                        # Release service after successful completion
                        self._release_relevance_service(service)
                        return result, service
                    except Exception:
                        # Release failed service
                        self._release_relevance_service(service)
            return False, service

    async def detect_sentiment(
        self, user_input: str, rating: Optional[int] = None, session_id: Optional[str] = None
    ) -> tuple[str, Optional[LLMAnalysisService]]:
        """Detect sentiment using load-balanced service with optimizations."""
        service = await self.get_sentiment_service()
        if not service:
            logger.error("No sentiment service available")
            return "neutral", None

        try:
            # Optimized call with pre-warmed connection
            result = await service.detect_sentiment(user_input=user_input, rating=rating, session_id=session_id)
            # Release service after successful completion
            self._release_sentiment_service(service)
            return result, service
        except Exception as e:
            logger.error("Sentiment analysis failed", error=str(e))

            # Release failed service
            self._release_sentiment_service(service)

            # Try with next service if available
            if len(self._sentiment_services) > 1:
                service = await self.get_sentiment_service()
                if service:
                    try:
                        result = await service.detect_sentiment(
                            user_input=user_input, rating=rating, session_id=session_id
                        )
                        # Release service after successful completion
                        self._release_sentiment_service(service)
                        return result, service
                    except Exception:
                        # Release failed service
                        self._release_sentiment_service(service)
            return "neutral", service

    async def analyze_review(
        self, user_input: str, category: str, criteria: str, session_id: Optional[str] = None
    ) -> tuple[list[str], str, Optional[LLMAnalysisService]]:
        """Analyze review using load-balanced service with MEDIUM priority."""
        # Create a future to be resolved when service becomes available
        future = asyncio.Future()

        # Create task wrapper with MEDIUM priority
        task_id = f"analysis-{self._task_counter}"
        self._task_counter += 1

        task_wrapper = TaskWrapper(
            priority=Priority.MEDIUM,
            task_id=task_id,
            task_type="analysis",
            func=lambda: None,  # Placeholder function
            args=(),
            kwargs={},
            timestamp=time.time(),
            future=future,
        )

        logger.info(f"Queuing analysis task {task_id} with priority {Priority.MEDIUM.name}")

        # Put the task wrapper in the unified priority queue
        await self._unified_waiting_queue.put(task_wrapper)

        # Wait for the service to be available
        logger.debug(f"Waiting for relevance service for analysis task {task_id}")
        service = await future
        if not service:
            logger.error("No relevance service available for review analysis")
            return [], "Анализ недоступен", None

        try:
            result = await service.analyze_review(
                user_input=user_input, category=category, criteria=criteria, session_id=session_id
            )
            # Release service after successful completion
            self._release_relevance_service(service)
            return result[0], result[1], service
        except Exception as e:
            logger.error("Review analysis failed", error=str(e))
            # Release failed service
            self._release_relevance_service(service)
            # Try with next service if available
            if len(self._relevance_services) > 1:
                # Create another task for retry
                retry_future = asyncio.Future()
                retry_task_id = f"analysis-retry-{self._task_counter}"
                self._task_counter += 1

                retry_task_wrapper = TaskWrapper(
                    priority=Priority.MEDIUM,
                    task_id=retry_task_id,
                    task_type="analysis",
                    func=lambda: None,
                    args=(),
                    kwargs={},
                    timestamp=time.time(),
                    future=retry_future,
                )

                await self._unified_waiting_queue.put(retry_task_wrapper)
                retry_service = await retry_future

                if retry_service:
                    try:
                        result = await retry_service.analyze_review(
                            user_input=user_input, category=category, criteria=criteria, session_id=session_id
                        )
                        # Release service after successful completion
                        self._release_relevance_service(retry_service)
                        return result[0], result[1], retry_service
                    except Exception:
                        # Release failed service
                        self._release_relevance_service(retry_service)
            return [], "Анализ недоступен", service

    async def process_relevance_and_sentiment_optimized(
        self, user_input: str, rating: Optional[int] = None, session_id: Optional[str] = None
    ) -> tuple[bool, Optional[str], Optional[LLMAnalysisService], Optional[LLMAnalysisService]]:
        """
        Optimized sequential processing of relevance and sentiment analysis.
        Returns: (is_relevant, sentiment, relevance_service, sentiment_service)
        """
        # Step 1: Check relevance
        is_relevant, relevance_service = await self.check_relevant_review(user_input, session_id)

        if not is_relevant:
            return False, None, relevance_service, None

        # Step 2: If relevant, detect sentiment
        sentiment, sentiment_service = await self.detect_sentiment(user_input, rating, session_id)

        return True, sentiment, relevance_service, sentiment_service

    def _create_task_wrapper(
        self,
        priority: Priority,
        task_id: str,
        task_type: str,
        func: Callable,
        args: Tuple,
        kwargs: Dict[str, Any],
        future: Optional[asyncio.Future] = None,
    ) -> TaskWrapper:
        """Create a task wrapper with priority and metadata."""
        return TaskWrapper(
            priority=priority,
            task_id=task_id,
            task_type=task_type,
            func=func,
            args=args,
            kwargs=kwargs,
            timestamp=time.time(),
            future=future,
        )

    async def shutdown(self):
        """Shutdown the LLM pool gracefully."""
        logger.info("Shutting down LLM pool...")
        self._stop_workers()

        # Wait for worker to finish
        if self._unified_worker_task:
            try:
                await self._unified_worker_task
            except asyncio.CancelledError:
                pass

        logger.info("LLM pool shutdown complete")

    def get_pool_status(self) -> Dict[str, Any]:
        """Get status of the LLM pool."""
        return {
            "relevance_services_count": len(self._relevance_services),
            "sentiment_services_count": len(self._sentiment_services),
            "has_primary_key": bool(self.settings.LLM_API_KEY),
            "has_secondary_key": bool(self.settings.LLM_API_KEY_2),
            "keys_different": self.settings.LLM_API_KEY != self.settings.LLM_API_KEY_2,
            "max_concurrent_requests": self._max_concurrent_requests,
            "max_wait_time": self._max_wait_time,
            "unified_waiting_queue_size": self._unified_waiting_queue.qsize(),
            "relevance_semaphore_available": self._relevance_semaphore._value if self._relevance_semaphore else 0,
            "sentiment_semaphore_available": self._sentiment_semaphore._value if self._sentiment_semaphore else 0,
            "priority_support": True,
            "available_priorities": [p.name for p in Priority],
            "unified_worker_running": self._running,
        }


# Global singleton instance
llm_pool = LLMPoolService()
