import glob
import os
from pathlib import Path
from urllib.parse import urlparse

import torch
from torch.hub import download_url_to_file, get_dir

from core.devicelib import get_optimal_device

import pickle
import collections
import sys
import traceback

import torch
import numpy
import _codecs
import zipfile
import re

# PyTorch 1.13 and later have _TypedStorage renamed to TypedStorage
TypedStorage = torch.storage.TypedStorage if hasattr(torch.storage, 'TypedStorage') else torch.storage._TypedStorage

allowed_zip_names = ["archive/data.pkl", "archive/version"]
allowed_zip_names_re = re.compile(r"^archive/data/\d+$")
module_in_gpu = None
cpu = torch.device("cpu")
device = gpu = get_optimal_device()


def send_everything_to_cpu():
    global module_in_gpu

    if module_in_gpu is not None:
        module_in_gpu.to(cpu)

    module_in_gpu = None


def friendly_name(file: str):
    if "http" in file:
        file = urlparse(file).path

    file = os.path.basename(file)
    model_name, extension = os.path.splitext(file)
    return model_name


def model_hash(filename):
    try:
        with open(filename, "rb") as file:
            import hashlib
            m = hashlib.sha256()

            file.seek(0x100000)
            m.update(file.read(0x10000))
            return m.hexdigest()[0:8]
    except FileNotFoundError:
        return 'NOFILE'


def load_models(model_path: Path, model_url: Path = None, command_path: Path = None, ext_filter=None, download_name=None) -> list:
    """
    A one-and done loader to try finding the desired models in specified directories.

    @param download_name: Specify to download from model_url immediately.
    @param model_url: If no other models are found, this will be downloaded on upscale.
    @param model_path: The location to store/find models in.
    @param command_path: A command-line argument to search for models in first.
    @param ext_filter: An optional list of filename extensions to filter by
    @return: A list of paths containing the desired model(s)
    """
    output = []

    if ext_filter is None:
        ext_filter = []

    try:
        places = []

        if command_path is not None and command_path != model_path:
            pretrained_path = os.path.join(command_path, 'experiments/pretrained_models')
            if os.path.exists(pretrained_path):
                print(f"Appending path: {pretrained_path}")
                places.append(pretrained_path)
            elif os.path.exists(command_path):
                places.append(command_path)

        places.append(model_path)

        for place in places:
            if os.path.exists(place):
                for file in glob.iglob(place + '**/**', recursive=True):
                    full_path = file
                    if os.path.isdir(full_path):
                        continue
                    if len(ext_filter) != 0:
                        model_name, extension = os.path.splitext(file)
                        if extension not in ext_filter:
                            continue
                    if file not in output:
                        output.append(full_path)

        if model_url is not None and len(output) == 0:
            if download_name is not None:
                dl = load_file_from_url(model_url, model_path, True, download_name)
                output.append(dl)
            else:
                output.append(model_url)

    except Exception:
        pass

    return output

def load_file_from_url(url, model_dir=None, progress=True, file_name=None):
    """Load file form http url, will download models if necessary.

    Ref:https://github.com/1adrianb/face-alignment/blob/master/face_alignment/utils.py

    Args:
        url (str): URL to be downloaded.
        model_dir (str): The path to save the downloaded model. Should be a full path. If None, use pytorch hub_dir.
            Default: None.
        progress (bool): Whether to show the download progress. Default: True.
        file_name (str): The downloaded file name. If None, use the file name in the url. Default: None.

    Returns:
        str: The path to the downloaded file.
    """
    if model_dir is None:  # use the pytorch hub_dir
        hub_dir = get_dir()
        model_dir = os.path.join(hub_dir, 'checkpoints')

    os.makedirs(model_dir, exist_ok=True)

    parts = urlparse(url)
    filename = os.path.basename(parts.path)
    if file_name is not None:
        filename = file_name
    cached_file = os.path.abspath(os.path.join(model_dir, filename))
    if not os.path.exists(cached_file):
        print(f'Downloading: "{url}" to {cached_file}\n')
        download_url_to_file(url, cached_file, hash_prefix=None, progress=progress)
    return cached_file

# def load_upscalers():
#     sd = shared.script_path
#
#     # We can only do this 'magic' method to dynamically load upscalers if they are referenced,
#     # so we'll try to import any _model.py files before looking in __subclasses__
#     modules_dir = os.path.join(sd, "modules")
#     for file in os.listdir(modules_dir):
#         if "_model.py" in file:
#             model_name = file.replace("_model.py", "")
#             full_model = f"modules.{model_name}_model"
#             try:
#                 importlib.import_module(full_model)
#             except:
#                 pass
#     datas = []
#     c_o = vars(shared.cmd_opts)
#     for cls in Upscaler.__subclasses__():
#         name = cls.__name__
#         module_name = cls.__module__
#         module = importlib.import_module(module_name)
#         class_ = getattr(module, name)
#         cmd_name = f"{name.lower().replace('upscaler', '')}_models_path"
#         opt_string = None
#         try:
#             if cmd_name in c_o:
#                 opt_string = c_o[cmd_name]
#         except:
#             pass
#         scaler = class_(opt_string)
#         for child in scaler.scalers:
#             datas.append(child)
#
#     shared.sd_upscalers = datas


def encode(*args):
    out = _codecs.encode(*args)
    return out


class RestrictedUnpickler(pickle.Unpickler):
    def persistent_load(self, saved_id):
        assert saved_id[0] == 'storage'
        return TypedStorage()

    def find_class(self, module, name):
        if module == 'collections' and name == 'OrderedDict':
            return getattr(collections, name)
        if module == 'torch._utils' and name in ['_rebuild_tensor_v2', '_rebuild_parameter']:
            return getattr(torch._utils, name)
        if module == 'torch' and name in ['FloatStorage', 'HalfStorage', 'IntStorage', 'LongStorage', 'DoubleStorage']:
            return getattr(torch, name)
        if module == 'torch.nn.modules.container' and name in ['ParameterDict']:
            return getattr(torch.nn.modules.container, name)
        if module == 'numpy.core.multiarray' and name == 'scalar':
            return numpy.core.multiarray.scalar
        if module == 'numpy' and name == 'dtype':
            return numpy.dtype
        if module == '_codecs' and name == 'encode':
            return encode
        if module == "pytorch_lightning.callbacks" and name == 'model_checkpoint':
            import pytorch_lightning.callbacks
            return pytorch_lightning.callbacks.model_checkpoint
        if module == "pytorch_lightning.callbacks.model_checkpoint" and name == 'ModelCheckpoint':
            import pytorch_lightning.callbacks.model_checkpoint
            return pytorch_lightning.callbacks.model_checkpoint.ModelCheckpoint
        if module == "__builtin__" and name == 'set':
            return set

        # Forbid everything else.
        raise pickle.UnpicklingError(f"global '{module}/{name}' is forbidden")


def check_zip_filenames(filename, names):
    for name in names:
        if name in allowed_zip_names:
            continue
        if allowed_zip_names_re.match(name):
            continue

        raise Exception(f"bad file inside {filename}: {name}")


def check_pt(filename):
    try:
        # new pytorch format is a zip file
        with zipfile.ZipFile(filename) as z:
            check_zip_filenames(filename, z.namelist())

            with z.open('archive/data.pkl') as file:
                unpickler = RestrictedUnpickler(file)
                unpickler.load()

    except zipfile.BadZipfile:
        # if it's not a zip file, it's an olf pytorch format, with five objects written to pickle
        with open(filename, "rb") as file:
            unpickler = RestrictedUnpickler(file)
            for i in range(5):
                unpickler.load()


def load(filename, *args, **kwargs):
    try:
        # if not shared.cmd_opts.disable_safe_unpickle:
        check_pt(filename)

    except Exception:
        print(f"Error verifying pickled file from {filename}:", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        print(f"\nThe file may be malicious, so the program is not going to read it.", file=sys.stderr)
        print(f"You can skip this check with --disable-safe-unpickle commandline argument.", file=sys.stderr)
        return None

    return unsafe_torch_load(filename, *args, **kwargs)


unsafe_torch_load = torch.load
torch.load = load
