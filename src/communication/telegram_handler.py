import logging
from telegram import Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

async def handle_document(update: Update, context: CallbackContext):
    document = update.message.document
    file_id = document.file_id
    file = await context.bot.get_file(file_id)
    await file.download()
    logger.info(f'Document {document.file_name} received and saved.')

async def handle_video(update: Update, context: CallbackContext):
    video = update.message.video
    file_id = video.file_id
    file = await context.bot.get_file(file_id)
    await file.download()
    logger.info(f'Video {video.file_name} received and saved.')

# Очередь для обработки сообщений
async def handle_message(update: Update, context: CallbackContext):
    if update.message.document:
        await handle_document(update, context)
    elif update.message.video:
        await handle_video(update, context)
    else:
        logger.warning('Received a message that is not a document or video.')