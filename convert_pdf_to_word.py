import pdfplumber
from docx import Document

def convert_pdf_to_word(pdf_path, word_path):
    document = Document()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            document.add_paragraph(page.extract_text())
    document.save(word_path)

if __name__ == '__main__':
    convert_pdf_to_word('договор.pdf', 'договор.docx')
