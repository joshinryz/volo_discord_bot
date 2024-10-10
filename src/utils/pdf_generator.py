import tempfile
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet

async def pdf_generator(transcriptions, logo_path=None):
    """
    Generates a PDF document with transcriptions and optional logo.

    :param transcriptions: List of transcriptions text to include in the PDF.
    :param logo_path: Optional path to a logo image to include in the PDF.
    :return: The path to the generated PDF file (temporary file).
    """
    # Create a temporary file in the .logs directory
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir="./.logs/pdfs") as tmp_file:
        pdf_file_path = tmp_file.name

    # Create the PDF document
    doc = SimpleDocTemplate(pdf_file_path, pagesize=A4)
    elements = []

    # Add custom logo (if available)
    if logo_path and os.path.exists(logo_path):
        logo = Image(logo_path, 2 * inch, 2 * inch)
        elements.append(logo)
        elements.append(Spacer(1, 12))

    # Add a title
    styles = getSampleStyleSheet()
    title = Paragraph("Transcription Report", styles["Title"])
    elements.append(title)
    elements.append(Spacer(1, 12))
    
    # Add the transcriptions
    for idx, transcription in enumerate(transcriptions):
        text = f"{idx+1}. {transcription}"
        elements.append(Paragraph(text, styles["Normal"]))
        elements.append(Spacer(1, 12))

    # Build the PDF
    doc.build(elements)

    return pdf_file_path
