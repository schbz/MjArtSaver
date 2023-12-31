import os
from dotenv import load_dotenv
import boto3
import discord
from discord.ext import commands
from discord.ui import Button, View
from discord import Embed
from datetime import datetime
import aiohttp
from io import BytesIO

# Load the .env file located in the same directory
load_dotenv()

# Obtain the values using os.getenv
IAM_USERNAME = os.getenv('IAM_USERNAME')
ACCESS_KEY_ID = os.getenv('ACCESS_KEY_ID')
SECRET_ACCESS_KEY = os.getenv('SECRET_ACCESS_KEY')
BOT_TOKEN = os.getenv('BOT_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
PROMPT_INCLUDE = os.getenv('PROMPT_INCLUDE')
AUTO_UPLOAD = os.getenv('AUTO_UPLOAD')

# AWS S3 Configuration
s3_client = boto3.client('s3', aws_access_key_id=ACCESS_KEY_ID, aws_secret_access_key=SECRET_ACCESS_KEY)
s3_bucket = S3_BUCKET_NAME
s3_path = ''
image_metadata = {}

# Constants
MIDJOURNEY_BOT_ID = "None"  # This gets automatically replaced with the mj bot's user ID


intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.members = True

class imageView(View):
    def __init__(self, files):
        super().__init__()
        for file in files:
            self.add_item(imageButton(label=file['Key'], custom_id=file['Key']))


class imageButton(Button):
    async def callback(self, interaction: discord.Interaction):
        filename = self.custom_id
        file_path = f'./tmp/{filename}'
        try:
            
            # Create the platform-independent path for the directory and ensure it exists
            directory_path = f"./tmp/{s3_path}"
            if not os.path.exists(directory_path):
                os.makedirs(directory_path)
            
            s3_full_path = f"{filename}"
            s3_client.download_file(s3_bucket, s3_full_path, file_path)
            with open(file_path, 'rb') as img:
                await interaction.response.send_message(f"File Name: **{filename}**", file=discord.File(img, filename))
        except Exception as e:
            await interaction.response.send_message(f'Error fetching the image: {str(e)}')

# Upload an image to s3 via async streaming
async def stream_to_s3(url, s3_full_path,text_prompt=None):
    global image_metadata
    old_metadata = image_metadata
    if PROMPT_INCLUDE and text_prompt:

        text_prompt = text_prompt.split(' - ')[0].replace("*", "").replace(" ", "-")

        image_metadata['Comments'] = text_prompt
        last_slash_index = s3_full_path.rfind('/')
        if len(text_prompt) > 80:
            text_prompt = text_prompt[:80]
        if last_slash_index == -1:  # If no '/' found
            modified_string = text_prompt + s3_full_path
        else:
            modified_string = s3_full_path[:last_slash_index + 1] + text_prompt + s3_full_path[last_slash_index + 1:]

        print(modified_string)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            # Check if the request was successful
            if response.status != 200:
                raise ValueError(f"Failed to fetch image from {url}. Status: {response.status}")

            # Convert bytes to a file-like object using BytesIO
            fileobj = BytesIO(await response.read())

            # Stream data directly to S3
            s3_client.upload_fileobj(fileobj, s3_bucket, modified_string, ExtraArgs={'Metadata': image_metadata})
    image_metadata = old_metadata
# Custom Help Command
class CustomHelpCommand(commands.HelpCommand):
    async def send_bot_help(self, mapping):
        embed = Embed(title="Help", description="List of commands:")
        for command in self.context.bot.commands:
            embed.add_field(name=f'{command.name}', value=command.help or "No description provided.", inline=False)
        await self.get_destination().send(embed=embed)


# Bot Class
class MyBot(commands.Bot):
    async def on_ready(self):
        global MIDJOURNEY_BOT_ID
        # Iterate through all guilds the bot is a member of
        for guild in self.guilds:
            # Iterate through all members in the guild
            for member in guild.members:
                # Check if the member is a bot and has "midjourney" in their name
                if member.bot and "midjourney" in member.name.lower():
                    MIDJOURNEY_BOT_ID = member.id
                    break

            # Break the outer loop if the bot ID is found
            if MIDJOURNEY_BOT_ID != "None":
                break
        
        print(f'{self.user} has connected to Discord! Midjourney Bot ID: {MIDJOURNEY_BOT_ID}')


    async def on_message(self, message):
        if message.author.id == MIDJOURNEY_BOT_ID and any(att.content_type.startswith('image/') for att in message.attachments):
            await self.handle_midjourney_bot_message(message)
        await super().on_message(message)

    async def handle_midjourney_bot_message(self, message):
        if AUTO_UPLOAD:
            try:
            # Generate a filename based on current time
                #await interaction.response.defer()
                #await interaction.followup.send('uploading image...')
                current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
                # Assuming .jpg extension for simplicity; can be enhanced
                image_name = f'{current_time}.jpg'
                #s3_full_path = os.path.join(s3_path, image_name)
                s3_full_path = f"{s3_path}/{image_name}"
                await stream_to_s3(f'{message.attachments[0]}', s3_full_path, message.content)
                await message.channel.send(f'Image {message.content[:15]} uploaded as {s3_full_path} in the bucket {s3_bucket}!')
                
            except Exception as e:
                await message.channel.send(f'Error uploading the image: {str(e)}')
            pass
        else:
            view = UploadView(message.channel)
            await message.channel.send('Would you like to upload the last image?', view=view)

bot = MyBot(command_prefix='!', help_command=CustomHelpCommand(), intents=intents)


# Upload View
class UploadView(View):
    def __init__(self, channel: discord.TextChannel):
        super().__init__()  # Make sure to call the parent's init
        self.channel = channel
    
    @discord.ui.button(label='Upload Last Image', style=discord.ButtonStyle.primary)
    async def upload_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        
        last_image = None
        last_text = None
        
        # Get the timestamp of when the message with the button was created.
        button_message_time = interaction.message.created_at

        # Get messages before the button message
        messages_before_button = []
        async for message in self.channel.history(before=button_message_time, limit=1):
            messages_before_button.append(message)

        # Among these messages, find the latest message from the bot with an image.
        for message in messages_before_button:
            if message.author.bot and message.attachments:
                last_image = message.attachments[0]
                last_text = message.content
                break

        if last_image:
            
            try:
            # Generate a filename based on current time
                await interaction.response.defer()
                await interaction.followup.send('uploading image...')
                current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
                # Assuming .jpg extension for simplicity; can be enhanced
                image_name = f'{current_time}.jpg'
                #s3_full_path = os.path.join(s3_path, image_name)
                s3_full_path = f"{s3_path}/{image_name}"
                await stream_to_s3(f'{last_image}', s3_full_path, last_text)
                await interaction.followup.send(f'Image uploaded as {s3_full_path} in the bucket {s3_bucket}!')
                
            except Exception as e:
                await self.channel.send(f'Error uploading the image: {str(e)}')
                return
            
        else:
            #await interaction.response.send('No recent image found.')
            await self.channel.send('No recent image found')

@bot.command(name='upload', help='Upload an image to the S3 bucket. Drag and drop the image and optionally provide a name. Usage: !upload <optional_name>')
async def upload(ctx, image_name_or_link: str = None):
    # Check if bucket is set
    if not s3_bucket:
        await ctx.send('Please set the S3 bucket first using !set_bucket')
        return
    
    # If a link is provided
    if image_name_or_link and image_name_or_link.startswith(('http://', 'https://')):
        try:
            # Generate a filename based on current time
            current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Assuming .jpg extension for simplicity; can be enhanced
            image_name = f'{current_time}.jpg'
            s3_full_path = f"{s3_path}/{image_name}"
            await stream_to_s3(image_name_or_link, s3_full_path)
            await ctx.send(f'Image uploaded as {s3_full_path} in the bucket {s3_bucket}!')
        except Exception as e:
            await ctx.send(f'Error uploading the image: {str(e)}')
            return

    # If an attachment is present in the message
    elif ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        # Generate a filename if none is provided
        if image_name_or_link is None:
            current_time = datetime.now().strftime('%Y%m%d_%H%M%S')
            file_extension = os.path.splitext(attachment.filename)[1]  # Extract extension from the original filename
            image_name_or_link = f"{current_time}{file_extension}"
        file_path = f'{image_name_or_link}'
        await attachment.save(file_path)
        
        # Upload to S3
        try:
            #s3_full_path = os.path.join(s3_path, image_name_or_link)
            s3_full_path = f"{s3_path}/{image_name_or_link}"
            s3_client.upload_file(file_path, s3_bucket, s3_full_path, ExtraArgs={'Metadata': image_metadata})
            await ctx.send(f'Image uploaded as {s3_full_path} in the bucket {s3_bucket}!')
        except Exception as e:
            await ctx.send(f'Error uploading the image: {str(e)}')
        finally:
            # Delete the image from the project folder (temporary location)
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting the file {file_path}: {str(e)}")
    else:
        await ctx.send('Please provide a valid image or image link.')

@bot.command(name='set_bucket', help='Set the S3 bucket and optionally the path. Usage: !set_bucket <bucket_name> [path]')
async def set_bucket(ctx, bucket: str, path: str = ''):
    global s3_bucket, s3_path
    s3_bucket = bucket
    s3_path = path
    await ctx.send(f'S3 bucket and path set to {s3_bucket}/{s3_path}' if s3_path else f'S3 bucket set to {s3_bucket}')

@bot.command(name='set_path', help='Set the S3 path. Usage: !set_path <path>')
async def change_path(ctx, path: str = ""):
    global s3_path
    s3_path = path
    await ctx.send(f'S3 path set to {s3_path}')

@bot.command(name='bucket', help='Display the current S3 bucket and path(if available).')
async def bucket(ctx):
    global s3_bucket, s3_path
    if s3_bucket and s3_path:
        await ctx.send(f'Current S3 location is: {s3_bucket}/{s3_path}')
    elif s3_bucket:
        await ctx.send(f'Current S3 bucket is: {s3_bucket}, path is empty')
    else:
        await ctx.send('No S3 bucket or path has been set.')

@bot.command(name='path', help='Display the current S3 path(if available).')
async def bucket(ctx):
    global s3_bucket, s3_path
    if s3_bucket and s3_path:
        await ctx.send(f'Current S3 location is: {s3_bucket}/{s3_path}')
    elif s3_bucket:
        await ctx.send(f'Current S3 bucket is: {s3_bucket}, path is empty')
    else:
        await ctx.send('No S3 bucket or path has been set.')

@bot.command(name='set_metadata', help='Set default metadata values for uploaded files: !set_metadata <key> <value>')
async def set_metadata(ctx, key: str, value: str):
    global image_metadata
    image_metadata[key] = value
    await ctx.send(f'Metadata set: {key} = {value}')

@bot.command(name='set_aws', help='Set AWS credentials. Usage: !set_aws <access_key_id> <secret_access_key>')
@commands.is_owner()  # Restrict this command to the bot owner
async def config_aws(ctx, access_key_id: str, secret_access_key: str):
    os.environ['AWS_ACCESS_KEY_ID'] = access_key_id
    os.environ['AWS_SECRET_ACCESS_KEY'] = secret_access_key
    await ctx.send('AWS credentials configured.')

@bot.command(name='toggle_auto_upload', help='Toggle the AUTO_UPLOAD setting.')
async def toggle_auto_upload(ctx):
    global AUTO_UPLOAD
    AUTO_UPLOAD = not AUTO_UPLOAD
    await ctx.send(f'AUTO_UPLOAD set to {AUTO_UPLOAD}')

@bot.command(name='toggle_prompt_include', help='Toggle the PROMPT_INCLUDE setting.')
async def toggle_prompt_include(ctx):
    global PROMPT_INCLUDE
    PROMPT_INCLUDE = not PROMPT_INCLUDE
    await ctx.send(f'PROMPT_INCLUDE set to {PROMPT_INCLUDE}')

@bot.command(name='list_images', help='List all images in the S3 bucket(or specific path if set)')
async def list_images(ctx):
    # Check if bucket is set
    if not s3_bucket:
        await ctx.send('Please set the S3 bucket first using !set_bucket')
        return

    # Fetch the list of objects in the bucket
    objects = s3_client.list_objects_v2(Bucket=s3_bucket)
    
    # Filter for images (assuming JPEG and PNG for simplicity)
    image_files = [obj['Key'] for obj in objects.get('Contents', []) if obj['Key'].endswith(('.jpg', '.jpeg', '.png'))]
    
    if not image_files:
        await ctx.send('No images found in the bucket.')
        return

    # Send the list of images to the channel
    images_list = '\n'.join(image_files)
    await ctx.send(f"images in the bucket:\n{images_list}")

@bot.command(name='get_image', help='Choose an image from the current location to download. Usage: !get_image <filename>')
async def get_image(ctx, filename: str = None):
    # Check if bucket is set
    if not s3_bucket:
        await ctx.send('Please set the S3 bucket first using !set_bucket')
        return

    if filename is None:  # If no filename is provided
        objects = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix=s3_path)
        image_files = [obj for obj in objects['Contents'] if isinstance(obj, dict) and obj.get('Key').endswith(('.jpg', '.jpeg', '.png'))]
        sorted_files = sorted(image_files, key=lambda x: x.get('LastModified', ''), reverse=True)[:10]
        if not sorted_files:
            await ctx.send('No images found in the bucket.')
            return
        view = imageView(sorted_files)
        await ctx.send('Select a image:', view=view)
    else:

        # Create the platform-independent path for the directory and ensure it exists
        directory_path = f"./{s3_path}"
        if not os.path.exists(directory_path):
            print('making '+ directory_path)
            os.makedirs(directory_path)


        file_path = f"{directory_path}/{filename}"
        #full_s3_path = os.path.join(s3_path, filename)
        s3_full_path = f"{s3_path}/{filename}"
        try:
            s3_client.download_file(s3_bucket, s3_full_path, file_path)
            with open(file_path, 'rb') as img:
                await ctx.send(f"File Name: **{filename}**", file=discord.File(img, filename))
        except Exception as e:
            await ctx.send(f'Error fetching the image: {str(e)}')

@bot.command(name='config', help='Display current config settings for the mjartsaver bot. Usage: !config')
@commands.is_owner()  # Restrict this command to the bot owner
async def config(ctx):
    # Display bot token partially masked for security
    masked_bot_token = BOT_TOKEN[:5] + "..." + BOT_TOKEN[-5:]
    # Display AWS keys partially masked for security
    masked_aws_access_key_id = ACCESS_KEY_ID[:4] + "..." + ACCESS_KEY_ID[-4:]
    masked_aws_secret_access_key = SECRET_ACCESS_KEY[:4] + "..." + SECRET_ACCESS_KEY[-4:]
    
    # Construct the message
    config_info = f"""
    Current settings for MJ Art Saver Bot:
    BOT_TOKEN: {masked_bot_token}
    AWS_ACCESS_KEY_ID: {masked_aws_access_key_id}
    AWS_SECRET_ACCESS_KEY: {masked_aws_secret_access_key}
    S3 Bucket: {s3_bucket}
    S3 Path: {s3_path}
    Image Metadata: {image_metadata}
    Auto Upload: {AUTO_UPLOAD}
    Include Prompt: {PROMPT_INCLUDE}
    """
    
    await ctx.send(f"```{config_info}```")


# Run Bot

bot.run(BOT_TOKEN)
