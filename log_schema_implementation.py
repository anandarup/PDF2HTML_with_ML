#!/usr/bin/env python3
"""
Enhanced logging implementation for PDF2HTML microservice.
Shows how to integrate the structured log schema with existing codebase.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from app_logging import get_logger, job_context, capture_infra_snapshot

log = get_logger(__name__)


@dataclass
class PDFMetadata:
    """Structured PDF metadata for logging."""
    filename: str
    pdf_stem: str
    file_size_bytes: int
    page_count: int
    has_ocr_text: bool = False
    is_scanned: bool = False
    pdf_version: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContentMetrics:
    """Content analysis metrics."""
    embedded_images_count: int = 0
    embedded_fonts_count: int = 0
    text_chars_count: int = 0
    tables_detected: int = 0
    figures_detected: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ComplexityScore:
    """Document complexity scoring."""
    overall: float = 0.0  # 0-100
    layout_density: float = 0.0  # 0-1
    font_variety: int = 0
    color_depth: str = "bw"  # "bw", "grayscale", "color"
    has_math_formulas: bool = False
    has_cjk_text: bool = False
    has_right_to_left: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StageTimings:
    """Stage-by-stage timing metrics."""
    preprocessing_ms: float = 0.0
    text_extraction_ms: float = 0.0
    font_analysis_ms: float = 0.0
    layout_analysis_ms: float = 0.0
    ocr_processing_ms: float = 0.0
    image_extraction_ms: float = 0.0
    dom_generation_ms: float = 0.0
    styling_application_ms: float = 0.0
    post_processing_ms: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceUsage:
    """Resource utilization metrics."""
    peak_memory_mb: float = 0.0
    avg_cpu_percent: float = 0.0
    disk_io_bytes: int = 0
    image_processing_time_ms: float = 0.0
    html_file_size_bytes: int = 0
    total_output_size_bytes: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConversionLogger:
    """Enhanced logging wrapper for structured logging."""
    
    def __init__(self, job_id: str, ref_id: Optional[str] = None):
        self.job_id = job_id
        self.ref_id = ref_id
        self.start_time = time.time()
        self.stage_timings = StageTimings()
        self.current_stage = None
        self.stage_start_time = None
        
    def log_conversion_start(self, pdf_metadata: PDFMetadata) -> None:
        """Log conversion start event."""
        with job_context(job_id=self.job_id, ref_id=self.ref_id, stage="queued"):
            log.info("Conversion job started", extra={
                "event_type": "conversion_start",
                "payload_metrics": {
                    "pdf_metadata": pdf_metadata.to_dict()
                }
            })
    
    def enter_stage(self, stage_name: str) -> None:
        """Mark entry into a processing stage."""
        if self.current_stage and self.stage_start_time:
            # Log completion of previous stage
            elapsed_ms = (time.time() - self.stage_start_time) * 1000
            self._update_stage_timing(self.current_stage, elapsed_ms)
            
            log.info(f"Stage transition: {self.current_stage} -> {stage_name}", extra={
                "event_type": "conversion_stage",
                "stage": stage_name,
                "performance_metrics": {
                    "stage_timings": {f"{self.current_stage}_ms": elapsed_ms}
                }
            })
        
        self.current_stage = stage_name
        self.stage_start_time = time.time()
        
        with job_context(job_id=self.job_id, stage=stage_name):
            log.debug(f"Entered stage: {stage_name}")
    
    def _update_stage_timing(self, stage_name: str, elapsed_ms: float) -> None:
        """Update timing for a completed stage."""
        stage_map = {
            "preprocessing": "preprocessing_ms",
            "text_extraction": "text_extraction_ms",
            "font_analysis": "font_analysis_ms",
            "layout_analysis": "layout_analysis_ms",
            "ocr_processing": "ocr_processing_ms",
            "image_extraction": "image_extraction_ms",
            "dom_generation": "dom_generation_ms",
            "styling_application": "styling_application_ms",
            "post_processing": "post_processing_ms"
        }
        
        if stage_name in stage_map:
            setattr(self.stage_timings, stage_map[stage_name], elapsed_ms)
    
    def log_conversion_complete(
        self,
        pdf_metadata: PDFMetadata,
        content_metrics: ContentMetrics,
        complexity_score: ComplexityScore,
        resource_usage: ResourceUsage
    ) -> None:
        """Log successful conversion completion."""
        total_time_ms = (time.time() - self.start_time) * 1000
        
        with job_context(job_id=self.job_id, ref_id=self.ref_id, stage="completed"):
            log.info("Conversion completed successfully", extra={
                "event_type": "conversion_complete",
                "payload_metrics": {
                    "pdf_metadata": pdf_metadata.to_dict(),
                    "content_metrics": content_metrics.to_dict(),
                    "complexity_score": complexity_score.to_dict()
                },
                "performance_metrics": {
                    "total_conversion_time_ms": total_time_ms,
                    "stage_timings": self.stage_timings.to_dict(),
                    "resource_usage": resource_usage.to_dict(),
                    "throughput_metrics": {
                        "pages_per_second": pdf_metadata.page_count / (total_time_ms / 1000),
                        "bytes_per_second": pdf_metadata.file_size_bytes / (total_time_ms / 1000)
                    }
                }
            })
    
    def log_conversion_failed(
        self,
        error_type: str,
        error_code: str,
        error_message: str,
        pdf_metadata: Optional[PDFMetadata] = None,
        missing_fonts: Optional[List[str]] = None,
        memory_exceeded: Optional[Dict[str, Any]] = None,
        timeout_exceeded: Optional[Dict[str, Any]] = None,
        stack_trace: Optional[str] = None
    ) -> None:
        """Log conversion failure with structured error information."""
        total_time_ms = (time.time() - self.start_time) * 1000
        
        error_data = {
            "error_type": error_type,
            "error_code": error_code,
            "error_message": error_message
        }
        
        if stack_trace:
            error_data["stack_trace"] = stack_trace
        
        if missing_fonts:
            error_data["missing_fonts"] = missing_fonts
        
        if memory_exceeded:
            error_data["memory_exceeded"] = memory_exceeded
        
        if timeout_exceeded:
            error_data["timeout_exceeded"] = timeout_exceeded
        
        extra_data = {
            "event_type": "conversion_failed",
            "errors_warnings": error_data
        }
        
        if pdf_metadata:
            extra_data["payload_metrics"] = {
                "pdf_metadata": pdf_metadata.to_dict()
            }
        
        extra_data["performance_metrics"] = {
            "total_conversion_time_ms": total_time_ms,
            "stage_timings": self.stage_timings.to_dict()
        }
        
        with job_context(job_id=self.job_id, ref_id=self.ref_id, stage="failed"):
            log.error(f"Conversion failed: {error_message}", extra=extra_data)
            
            # Capture infra snapshot for debugging
            capture_infra_snapshot(
                "conversion_failed",
                job_id=self.job_id,
                error_type=error_type,
                error_code=error_code
            )
    
    def log_warning(self, code: str, message: str, severity: str = "medium") -> None:
        """Log a warning with structured format."""
        with job_context(job_id=self.job_id, ref_id=self.ref_id):
            log.warning(message, extra={
                "warnings": [{
                    "code": code,
                    "message": message,
                    "severity": severity
                }]
            })


# Example usage in existing convert.py
def enhanced_convert_pdf_to_html(
    pdf_path: str,
    output_dir: str,
    job_id: str,
    ref_id: Optional[str] = None
) -> bool:
    """
    Enhanced version of convert_pdf_to_html with structured logging.
    """
    logger = ConversionLogger(job_id=job_id, ref_id=ref_id)
    
    try:
        # 1. Gather PDF metadata
        pdf_file = Path(pdf_path)
        pdf_metadata = PDFMetadata(
            filename=pdf_file.name,
            pdf_stem=pdf_file.stem,
            file_size_bytes=pdf_file.stat().st_size,
            page_count=0  # Would be determined by PDF parsing
        )
        
        logger.log_conversion_start(pdf_metadata)
        
        # 2. Preprocessing stage
        logger.enter_stage("preprocessing")
        # ... preprocessing logic ...
        time.sleep(0.5)  # Simulated processing
        
        # 3. Text extraction stage
        logger.enter_stage("text_extraction")
        # ... text extraction logic ...
        time.sleep(2.0)
        
        # Simulate missing fonts error
        missing_fonts = ["HelveticaNeue-Light", "Futura-Bold"]
        if missing_fonts:
            logger.log_conversion_failed(
                error_type="missing_fonts",
                error_code="PDF_FONT_MISSING",
                error_message=f"Missing fonts: {', '.join(missing_fonts)}",
                pdf_metadata=pdf_metadata,
                missing_fonts=missing_fonts,
                stack_trace="Traceback... (simulated)"
            )
            return False
        
        # 4. Continue with other stages...
        logger.enter_stage("layout_analysis")
        time.sleep(1.5)
        
        logger.enter_stage("image_extraction")
        time.sleep(1.0)
        
        logger.enter_stage("dom_generation")
        time.sleep(2.0)
        
        # 5. Log successful completion
        content_metrics = ContentMetrics(
            embedded_images_count=8,
            embedded_fonts_count=2,
            text_chars_count=45000,
            tables_detected=3,
            figures_detected=4
        )
        
        complexity_score = ComplexityScore(
            overall=42.5,
            layout_density=0.56,
            font_variety=3,
            color_depth="grayscale",
            has_math_formulas=False
        )
        
        resource_usage = ResourceUsage(
            peak_memory_mb=342.8,
            avg_cpu_percent=38.2,
            html_file_size_bytes=1567890,
            total_output_size_bytes=3456789
        )
        
        logger.log_conversion_complete(
            pdf_metadata=pdf_metadata,
            content_metrics=content_metrics,
            complexity_score=complexity_score,
            resource_usage=resource_usage
        )
        
        return True
        
    except MemoryError as e:
        # Handle memory limit exceeded
        logger.log_conversion_failed(
            error_type="memory_error",
            error_code="MEMORY_LIMIT_EXCEEDED",
            error_message=str(e),
            pdf_metadata=pdf_metadata,
            memory_exceeded={
                "limit_mb": 1024,
                "usage_mb": 1542.8,
                "stage": logger.current_stage
            },
            stack_trace="Traceback... (simulated)"
        )
        return False
        
    except Exception as e:
        # Handle other exceptions
        logger.log_conversion_failed(
            error_type="extraction_error",
            error_code="PDF_EXTRACTION_FAILED",
            error_message=str(e),
            pdf_metadata=pdf_metadata,
            stack_trace="Traceback... (simulated)"
        )
        return False


if __name__ == "__main__":
    # Example usage
    success = enhanced_convert_pdf_to_html(
        pdf_path="/opt/pdf2html/app/uploads/sample.pdf",
        output_dir="/opt/pdf2html/app/output/sample",
        job_id="test1234",
        ref_id="test-ref-123"
    )
    
    print(f"Conversion {'succeeded' if success else 'failed'}")