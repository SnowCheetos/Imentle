import io
import gc
import pika
import redis
import torch
import platform

from PIL import Image
from diffusers import DiffusionPipeline

class DiffuserService:
    """
    This class is the implementation of the diffuser microservice.
    It should perform inference using prompts obtained from the RabbitMQ queue.
    """
    def __init__(
            self,
            image_size=(512, 512),
            diffuser_steps=50,
            diffuser_model='stabilityai/stable-diffusion-2-1', 
            # diffuser_model='stabilityai/stable-diffusion-xl-base-1.0',
            rabbit_host='localhost'
        ) -> None:
        
        self.height, self.width = image_size
        self.diffuser_steps = diffuser_steps
        
        self.cuda_available = torch.cuda.is_available()

        if platform.system() == "Darwin": self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        else: self.device = 'cuda' if self.cuda_available else 'cpu'

        self.dtype = torch.float16 if self.cuda_available else torch.float32
        
        self.pipeline = DiffusionPipeline.from_pretrained(
            diffuser_model, 
            torch_dtype=self.dtype, 
            use_safetensors=True
        )
        self.pipeline.to(self.device)

        self.redis_conn = redis.Redis()
        self.redis_conn.hset('image', 'status', 'idle')

        self.connection = pika.BlockingConnection(pika.ConnectionParameters(rabbit_host))
        self.channel = self.connection.channel(channel_number=73)
        self.channel.queue_declare(queue='diffuser_service')

    def encode_image(self, image: Image.Image) -> bytes:
        image_bytes_io = io.BytesIO()
        image.save(image_bytes_io, format='JPEG')
        image_bytes = image_bytes_io.getvalue()
        return image_bytes

    def generate_image(self, prompt: str) -> Image.Image:
        return self.pipeline(
            prompt=f'A painting of {prompt}',
            negative_prompt='blurry and bad',
            num_inference_steps=self.diffuser_steps,
            height=self.height,
            width=self.width
        ).images[0]
    
    def callback(self, ch, method, properties, body):
        self.redis_conn.hset('image', 'status', 'busy')
        
        image = self.generate_image(body.decode())
        encoding = self.encode_image(image)
        
        self.redis_conn.hset('image', mapping={'next': encoding, 'status': 'idle'})

        gc.collect()
        ch.basic_ack(delivery_tag=method.delivery_tag)

    def start(self):
        try:
            self.channel.basic_consume(queue='diffuser_service', on_message_callback=self.callback)
            self.channel.start_consuming()
        finally:
            self.channel.close()
            self.connection.close()


if __name__ == '__main__':
    service = DiffuserService()
    service.start()