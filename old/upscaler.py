import os
from abc import abstractmethod

import PIL
from PIL import Image

from modules import shared
from core import modellib

LANCZOS = (Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
from core.paths import modeldir


class Upscaler:
    name = None
    model_path = None
    model_name = None
    model_url = None
    enable = True
    filter = None
    model = None
    user_path = None
    scalers: []
    tile = True

    def __init__(self, create_dirs=False):
        self.mod_pad_h = None
        self.tile_size = shared.opts.ESRGAN_tile
        self.tile_pad = shared.opts.ESRGAN_tile_overlap
        self.device = shared.device
        self.img = None
        self.output = None
        self.scale = 1
        self.half = not shared.cmd_opts.no_half
        self.pre_pad = 0
        self.mod_scale = None

        if self.model_path is None and self.name:
            self.model_path = os.path.join(modeldir, self.name)
        if self.model_path and create_dirs:
            os.makedirs(self.model_path, exist_ok=True)

        try:
            import cv2
            self.can_tile = True
        except:
            pass

    @abstractmethod
    def do_upscale(self, img: PIL.Image, selected_model: str):
        return img

    def upscale(self, img: PIL.Image, scale: int, selected_model: str = None):
        self.scale = scale
        dest_w = img.width * scale
        dest_h = img.height * scale
        for i in range(3):
            if img.width >= dest_w and img.height >= dest_h:
                break
            img = self.do_upscale(img, selected_model)
        if img.width != dest_w or img.height != dest_h:
            img = img.resize((int(dest_w), int(dest_h)), resample=LANCZOS)

        return img

    @abstractmethod
    def load_model(self, path: str):
        pass

    def find_models(self, ext_filter=None) -> list:
        return modellib.load_models(model_path=self.model_path, model_url=self.model_url, command_path=self.user_path)

    def update_status(self, prompt):
        print(f"\nextras: {prompt}", file=shared.progress_print_out)


class UpscalerData:
    name = None
    data_path = None
    scale: int = 4
    scaler: Upscaler = None
    model: None

    def __init__(self, name: str, path: str, upscaler: Upscaler = None, scale: int = 4, model=None):
        self.name = name
        self.data_path = path
        self.scaler = upscaler
        self.scale = scale
        self.model = model


