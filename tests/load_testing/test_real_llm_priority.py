"""
A real LLM Pool prioritization test using real API calls.

The test sends all tasks (relevance, sentiment, analysis) simultaneously to the LLM Pool,
and the Pool manages the prioritization itself:
- HIGH priority tasks (relevance, sentiment) are processed first for all reviews
- MEDIUM priority tasks (analysis) are processed only after HIGH priority tasks
- Analysis tasks are only created for negative reviews
- If the analysis takes a long time, the Pool can process the relevance/sentiment of other reviews.

This simulates the real operation of a system where all tasks arrive at the same time,
but the Pool ensures proper prioritization.
"""

import asyncio
import csv
import time
import structlog
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


logger = structlog.get_logger(__name__)


@dataclass
class TaskExecution:
    """Execution of a task with timestamps"""

    task_id: str
    priority: str
    task_type: str  # 'relevance', 'sentiment', 'analysis'
    start_time: float
    end_time: float
    duration: float
    review_id: str
    zone_name: str
    success: bool
    error: Optional[str] = None
    result_data: Optional[Dict] = None


@dataclass
class PriorityTestStats:
    """Statistics of the prioritization test"""

    total_tasks: int = 0
    high_priority_tasks: int = 0
    medium_priority_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    total_duration: float = 0.0
    high_priority_avg_duration: float = 0.0
    medium_priority_avg_duration: float = 0.0
    priority_violations: int = 0


class RealLLMPriorityTester:
    """Tester with real LLM calls"""

    def __init__(self):
        self.llm_pool = None
        self.executions: List[TaskExecution] = []
        self.stats = PriorityTestStats()
        self.task_counter = 0

    async def initialize_llm_pool(self):
        """Initialization of LLM Pool"""
        try:
            # Import llm_pool after event loop is running
            from app.services.llm.llm_pool import llm_pool

            self.llm_pool = llm_pool

            # Check the status of the pool
            pool_status = self.llm_pool.get_pool_status()
            logger.info("LLM Pool initialized", status=pool_status)

        except Exception as e:
            logger.error(f"Failed to initialize LLM Pool: {e}")
            raise

    def load_reviews_from_csv(self, csv_file: str) -> List[Dict[str, Any]]:
        """Loading reviews from CSV file"""
        reviews = []
        try:
            with open(csv_file, "r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                for row in reader:
                    if row.get("review_text", "").strip():
                        reviews.append(
                            {
                                "review_id": row.get("review_id", ""),
                                "zone_name": row.get("zone_name", ""),
                                "review_text": row.get("review_text", ""),
                                "sentiment": row.get("sentiment", ""),
                                "tags_str": row.get("tags_str", ""),
                                "recommendation": row.get("recommendation", ""),
                            }
                        )
            logger.info(f"Loaded {len(reviews)} reviews from {csv_file}")
            return reviews
        except Exception as e:
            logger.error(f"Failed to load reviews from {csv_file}: {e}")
            return []

    async def execute_relevance_task(self, review: Dict[str, Any]) -> TaskExecution:
        """Execution of the relevance check task (HIGH priority)"""
        task_id = f"relevance_{self.task_counter}_{review['review_id']}"
        self.task_counter += 1

        start_time = time.time()

        try:
            logger.info(f"🚀 Starting relevance task {task_id} (HIGH priority)")

            is_relevant, _ = await self.llm_pool.check_relevant_review(
                user_input=review["review_text"], session_id=f"test_{task_id}"
            )

            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="HIGH",
                task_type="relevance",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=True,
                result_data={"is_relevant": is_relevant},
            )

            logger.info(f"✅ Completed relevance task {task_id} in {duration:.3f}s, relevant: {is_relevant}")
            return execution

        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="HIGH",
                task_type="relevance",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=False,
                error=str(e),
            )

            logger.error(f"Failed relevance task {task_id}: {e}")
            return execution

    async def execute_sentiment_task(self, review: Dict[str, Any]) -> TaskExecution:
        """Execution of the sentiment detection task (HIGH priority)"""
        task_id = f"sentiment_{self.task_counter}_{review['review_id']}"
        self.task_counter += 1

        start_time = time.time()

        try:
            logger.info(f"🚀 Starting sentiment task {task_id} (HIGH priority)")

            sentiment, _ = await self.llm_pool.detect_sentiment(
                user_input=review["review_text"], rating=3, session_id=f"test_{task_id}"
            )

            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="HIGH",
                task_type="sentiment",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=True,
                result_data={"sentiment": sentiment},
            )

            logger.info(f"✅ Completed sentiment task {task_id} in {duration:.3f}s, sentiment: {sentiment}")
            return execution

        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="HIGH",
                task_type="sentiment",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=False,
                error=str(e),
            )

            logger.error(f"Failed sentiment task {task_id}: {e}")
            return execution

    async def execute_analysis_task(self, review: Dict[str, Any]) -> TaskExecution:
        """Execution of the analysis task (MEDIUM priority)"""
        task_id = f"analysis_{self.task_counter}_{review['review_id']}"
        self.task_counter += 1

        start_time = time.time()

        try:
            logger.info(f"🚀 Starting analysis task {task_id} (MEDIUM priority)")

            tags, recommendation, _ = await self.llm_pool.analyze_review(
                user_input=review["review_text"],
                category=review["zone_name"],
                criteria="Тестовые критерии для анализа отзыва",
                session_id=f"test_{task_id}",
            )

            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="MEDIUM",
                task_type="analysis",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=True,
                result_data={
                    "tags": tags,
                    "recommendation": recommendation[:100] + "..." if len(recommendation) > 100 else recommendation,
                },
            )

            logger.info(f"✅ Completed analysis task {task_id} in {duration:.3f}s")
            return execution

        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time

            execution = TaskExecution(
                task_id=task_id,
                priority="MEDIUM",
                task_type="analysis",
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                review_id=review["review_id"],
                zone_name=review["zone_name"],
                success=False,
                error=str(e),
            )

            logger.error(f"Failed analysis task {task_id}: {e}")
            return execution

    async def run_priority_test(self, reviews: List[Dict[str, Any]]) -> List[TaskExecution]:
        """Starting the prioritization test - sending all tasks simultaneously to the LLM Pool"""
        logger.info(f"Starting priority test with {len(reviews)} reviews")

        # Count the negative reviews
        negative_reviews = [r for r in reviews if r.get("sentiment", "").lower() == "negative"]
        logger.info(f"Reviews breakdown: {len(reviews)} total, {len(negative_reviews)} negative (will have analysis)")

        # Create all tasks simultaneously for each review
        all_tasks = []

        for review in reviews:
            # For each review, create tasks:
            # 1. Relevance (HIGH priority) - for all reviews
            all_tasks.append(self.execute_relevance_task(review))

            # 2. Sentiment (HIGH priority) - for all reviews
            all_tasks.append(self.execute_sentiment_task(review))

            # 3. Analysis (MEDIUM priority) - only for negative reviews
            if review.get("sentiment", "").lower() == "negative":
                all_tasks.append(self.execute_analysis_task(review))

        # Count the tasks by type
        high_tasks = 0
        medium_tasks = 0

        for review in reviews:
            # Relevance (HIGH) - for all reviews
            high_tasks += 1

            # Sentiment (HIGH) - for all reviews
            high_tasks += 1

            # Analysis (MEDIUM) - only for negative reviews
            if review.get("sentiment", "").lower() == "negative":
                medium_tasks += 1

        logger.info(f"Created {len(all_tasks)} total tasks:")
        logger.info(f"  - HIGH priority (relevance + sentiment): {high_tasks} tasks")
        logger.info(f"  - MEDIUM priority (analysis): {medium_tasks} tasks")
        logger.info("Sending all tasks to LLM Pool simultaneously...")
        logger.info("LLM Pool will handle prioritization internally (HIGH tasks first, then MEDIUM)")

        # Send all tasks simultaneously to the LLM Pool
        # LLM Pool will handle the prioritization internally (HIGH tasks first, then MEDIUM)
        start_time = time.time()
        logger.info("Sending all tasks to LLM Pool for concurrent processing with prioritization...")

        executions = await asyncio.gather(*all_tasks, return_exceptions=True)
        end_time = time.time()

        # Process the results
        processed_executions = []
        for execution in executions:
            if isinstance(execution, TaskExecution):
                processed_executions.append(execution)
            else:
                logger.error(f"Task failed with exception: {execution}")

        total_time = end_time - start_time
        logger.info(f"All tasks completed in {total_time:.3f}s")

        return processed_executions

    def analyze_priority_compliance(self, executions: List[TaskExecution]) -> PriorityTestStats:
        """Analysis of priority compliance"""
        stats = PriorityTestStats()

        if not executions:
            return stats

        # Basic statistics
        stats.total_tasks = len(executions)
        stats.successful_tasks = sum(1 for e in executions if e.success)
        stats.failed_tasks = stats.total_tasks - stats.successful_tasks

        high_priority_executions = [e for e in executions if e.priority == "HIGH"]
        medium_priority_executions = [e for e in executions if e.priority == "MEDIUM"]

        stats.high_priority_tasks = len(high_priority_executions)
        stats.medium_priority_tasks = len(medium_priority_executions)

        # Calculation of average execution times
        if high_priority_executions:
            stats.high_priority_avg_duration = sum(e.duration for e in high_priority_executions) / len(
                high_priority_executions
            )

        if medium_priority_executions:
            stats.medium_priority_avg_duration = sum(e.duration for e in medium_priority_executions) / len(
                medium_priority_executions
            )

        # Total execution time
        if executions:
            stats.total_duration = max(e.end_time for e in executions) - min(e.start_time for e in executions)

        # Analysis of priority - check that HIGH tasks started before MEDIUM
        if high_priority_executions and medium_priority_executions:
            # Find the earliest start time of HIGH tasks
            earliest_high_start = min(e.start_time for e in high_priority_executions)

            # Find the latest start time of HIGH tasks
            latest_high_start = max(e.start_time for e in high_priority_executions)

            # Check that all MEDIUM tasks started after the earliest HIGH task
            violations = [e for e in medium_priority_executions if e.start_time < earliest_high_start]
            stats.priority_violations = len(violations)

            if violations:
                logger.warning(
                    f"Priority violations detected: {len(violations)} MEDIUM tasks started before any HIGH tasks started"
                )
                for violation in violations[:5]:  # Show the first 5 violations
                    logger.warning(
                        f"Violation: {violation.task_id} started at {violation.start_time:.3f}, earliest HIGH started at {earliest_high_start:.3f}"
                    )
            else:
                logger.info(
                    f"Priority compliance: All {len(medium_priority_executions)} MEDIUM tasks started after HIGH tasks (earliest HIGH: {earliest_high_start:.3f}, latest HIGH: {latest_high_start:.3f})"
                )

        return stats

    def print_detailed_statistics(self, stats: PriorityTestStats, executions: List[TaskExecution]):
        """Printing detailed statistics"""
        print("\n" + "=" * 50)
        print("DETAILED STATISTICS OF THE PRIORITIZATION TEST OF THE LLM POOL")
        print("=" * 50)
        print("\nHOW THE TEST WORKS:")
        print("  1. Send all tasks simultaneously to the LLM Pool")
        print("  2. LLM Pool itself determines the priorities and order of processing")
        print("  3. HIGH priority (relevance, sentiment) are processed first")
        print("  4. MEDIUM priority (analysis) are processed only after HIGH")
        print("  5. If analysis is long, the Pool can process the relevance/sentiment of other reviews")

        print("\nTOTAL STATISTICS:")
        print(f"  Total tasks: {stats.total_tasks}")
        print(
            f"  Successfully completed: {stats.successful_tasks} ({stats.successful_tasks / stats.total_tasks * 100:.1f}%)"
        )
        print(f"  Errors: {stats.failed_tasks} ({stats.failed_tasks / stats.total_tasks * 100:.1f}%)")
        print(f"  Total execution time: {stats.total_duration:.3f}s")

        print("\nPRIORITIES:")
        print(f"  HIGH priority: {stats.high_priority_tasks} tasks")
        print(f"  MEDIUM priority: {stats.medium_priority_tasks} tasks")
        print(f"  Average time of HIGH tasks: {stats.high_priority_avg_duration:.3f}s")
        print(f"  Average time of MEDIUM tasks: {stats.medium_priority_avg_duration:.3f}s")

        # Detailed information by task types
        task_types = {}
        for execution in executions:
            task_type = execution.task_type
            if task_type not in task_types:
                task_types[task_type] = {"count": 0, "success": 0, "total_duration": 0}
            task_types[task_type]["count"] += 1
            if execution.success:
                task_types[task_type]["success"] += 1
            task_types[task_type]["total_duration"] += execution.duration

        print("\n📋 DETAILED INFORMATION BY TASK TYPES:")
        for task_type, data in task_types.items():
            avg_duration = data["total_duration"] / data["count"]
            success_rate = data["success"] / data["count"] * 100
            print(
                f"  {task_type.upper()}: {data['count']} задач, {success_rate:.1f}% успех, {avg_duration:.3f}s среднее время"
            )

        print("=" * 500)

    def save_results_to_csv(self, executions: List[TaskExecution], filename: str):
        """Saving results to CSV file in execution order"""
        try:
            # Sort tasks by start time of execution
            sorted_executions = sorted(executions, key=lambda x: x.start_time)

            with open(filename, "w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(
                    [
                        "execution_order",
                        "task_id",
                        "priority",
                        "task_type",
                        "start_time",
                        "end_time",
                        "duration",
                        "review_id",
                        "zone_name",
                        "success",
                        "error",
                        "result_data",
                    ]
                )

                for i, execution in enumerate(sorted_executions, 1):
                    writer.writerow(
                        [
                            i,  # Execution order
                            execution.task_id,
                            execution.priority,
                            execution.task_type,
                            execution.start_time,
                            execution.end_time,
                            execution.duration,
                            execution.review_id,
                            execution.zone_name,
                            execution.success,
                            execution.error or "",
                            json.dumps(execution.result_data) if execution.result_data else "",
                        ]
                    )

            logger.info(f"Results saved to {filename} in execution order")

        except Exception as e:
            logger.error(f"Failed to save results: {e}")


async def main():
    """Main function of the test"""
    tester = RealLLMPriorityTester()

    try:
        # Initialization of LLM Pool
        await tester.initialize_llm_pool()

        # Loading reviews from CSV files
        csv_files = ["analyzed_negative_reviews_20250914_222550.csv", "analyzed_negative_reviews_20250915_165327.csv"]

        all_reviews = []
        for csv_file in csv_files:
            reviews = tester.load_reviews_from_csv(csv_file)
            all_reviews.extend(reviews)

        if not all_reviews:
            logger.error("No reviews loaded, exiting")
            return

        # Limit the number of reviews for testing (to avoid overloading the API)
        test_reviews = all_reviews  # slice(50, 100, ...)
        logger.info(f"Testing with {len(test_reviews)} reviews (out of {len(all_reviews)} total)")

        # Starting the prioritization test
        executions = await tester.run_priority_test(test_reviews)

        # Analysis of results
        stats = tester.analyze_priority_compliance(executions)
        tester.print_detailed_statistics(stats, executions)

        # Saving results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = f"real_llm_priority_test_{timestamp}.csv"
        tester.save_results_to_csv(executions, results_file)

        logger.info("Priority test completed successfully")

    except Exception as e:
        logger.error(f"Priority test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
