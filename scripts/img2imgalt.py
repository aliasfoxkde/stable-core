from collections import namedtuple

import numpy as np
from tqdm import trange

from core import plugins as scripts
import gradio as gr

from modules import processing
import shared

import torch
import k_diffusion as K


def find_noise_for_image(p, cond, uncond, cfg_scale, steps):
    x = p.init_latent

    s_in = x.new_ones([x.shape[0]])
    dnw = K.external.CompVisDenoiser(shared.sd_model)
    sigmas = dnw.get_sigmas(steps).flip(0)

    shared.state.sampling_steps = steps

    for i in trange(1, len(sigmas)):
        shared.state.sampling_step += 1

        x_in = torch.cat([x] * 2)
        sigma_in = torch.cat([sigmas[i] * s_in] * 2)
        cond_in = torch.cat([uncond, cond])

        c_out, c_in = [K.utils.append_dims(k, x_in.ndim) for k in dnw.get_scalings(sigma_in)]
        t = dnw.sigma_to_t(sigma_in)

        eps = shared.sd_model.apply_model(x_in * c_in, t, cond=cond_in)
        denoised_uncond, denoised_cond = (x_in + eps * c_out).chunk(2)

        denoised = denoised_uncond + (denoised_cond - denoised_uncond) * cfg_scale

        d = (x - denoised) / sigmas[i]
        dt = sigmas[i] - sigmas[i - 1]

        x = x + d * dt

        sd_samplers.store_latent(x)

        # This shouldn't be necessary, but solved some VRAM issues
        del x_in, sigma_in, cond_in, c_out, c_in, t,
        del eps, denoised_uncond, denoised_cond, denoised, d, dt

    shared.state.nextjob()

    return x / x.std()


Cached = namedtuple("Cached", ["noise", "cfg_scale", "steps", "latent", "original_prompt", "original_negative_prompt", "sigma_adjustment"])


# Based on changes suggested by briansemrau in https://github.com/AUTOMATIC1111/stable-diffusion-webui/issues/736
def find_noise_for_image_sigma_adjustment(p, cond, uncond, cfg_scale, steps):
    x = p.init_latent

    s_in = x.new_ones([x.shape[0]])
    dnw = K.external.CompVisDenoiser(shared.sd_model)
    sigmas = dnw.get_sigmas(steps).flip(0)

    shared.state.sampling_steps = steps

    for i in trange(1, len(sigmas)):
        shared.state.sampling_step += 1

        x_in = torch.cat([x] * 2)
        sigma_in = torch.cat([sigmas[i - 1] * s_in] * 2)
        cond_in = torch.cat([uncond, cond])

        c_out, c_in = [K.utils.append_dims(k, x_in.ndim) for k in dnw.get_scalings(sigma_in)]

        if i == 1:
            t = dnw.sigma_to_t(torch.cat([sigmas[i] * s_in] * 2))
        else:
            t = dnw.sigma_to_t(sigma_in)

        eps = shared.sd_model.apply_model(x_in * c_in, t, cond=cond_in)
        denoised_uncond, denoised_cond = (x_in + eps * c_out).chunk(2)

        denoised = denoised_uncond + (denoised_cond - denoised_uncond) * cfg_scale

        if i == 1:
            d = (x - denoised) / (2 * sigmas[i])
        else:
            d = (x - denoised) / sigmas[i - 1]

        dt = sigmas[i] - sigmas[i - 1]
        x = x + d * dt

        sd_samplers.store_latent(x)

        # This shouldn't be necessary, but solved some VRAM issues
        del x_in, sigma_in, cond_in, c_out, c_in, t,
        del eps, denoised_uncond, denoised_cond, denoised, d, dt

    shared.state.nextjob()

    return x / sigmas[-1]


class Script(scripts.Plugin):
    def __init__(self):
        self.cache = None

    def title(self):
        return "img2img alternative test"

    def show(self, is_img2img):
        return is_img2img

    def ui(self, is_img2img):
        original_prompt = gr.Textbox(label="Original prompt", lines=1)
        original_negative_prompt = gr.Textbox(label="Original negative prompt", lines=1)
        cfg = gr.Slider(label="Decode CFG scale", minimum=0.0, maximum=15.0, step=0.1, value=1.0)
        st = gr.Slider(label="Decode steps", minimum=1, maximum=150, step=1, value=50)
        randomness = gr.Slider(label="Randomness", minimum=0.0, maximum=1.0, step=0.01, value=0.0)
        sigma_adjustment = gr.Checkbox(label="Sigma adjustment for finding noise for image", value=False)
        return [original_prompt, original_negative_prompt, cfg, st, randomness, sigma_adjustment]

    def run(self, p, original_prompt, original_negative_prompt, cfg, st, randomness, sigma_adjustment):


        def sample_extra(conditioning, unconditional_conditioning, seeds, subseeds, subseed_strength):
            lat = (p.init_latent.cpu().numpy() * 10).astype(int)

            same_params = self.cache is not None and self.cache.cfg == cfg and self.cache.steps == st \
                          and self.cache.original_prompt == original_prompt \
                          and self.cache.original_negative_prompt == original_negative_prompt \
                          and self.cache.sigma_adjustment == sigma_adjustment
            same_everything = same_params and self.cache.latent.shape == lat.shape and np.abs(self.cache.latent-lat).sum() < 100

            if same_everything:
                rec_noise = self.cache.noise
            else:
                shared.state.job_count += 1
                cond = p.model.get_learned_conditioning(p.batch_size * [original_prompt])
                uncond = p.model.get_learned_conditioning(p.batch_size * [original_negative_prompt])
                if sigma_adjustment:
                    rec_noise = find_noise_for_image_sigma_adjustment(p, cond, uncond, cfg, st)
                else:
                    rec_noise = find_noise_for_image(p, cond, uncond, cfg, st)
                self.cache = Cached(rec_noise, cfg, st, lat, original_prompt, original_negative_prompt, sigma_adjustment)

            rand_noise = processing.create_random_tensors(p.init_latent.shape[1:], seeds=seeds, subseeds=subseeds, subseed_strength=p.subseed_pow, seed_resize_from_h=p.resize_from_h, seed_resize_from_w=p.resize_from_w, p=p)

            combined_noise = ((1 - randomness) * rec_noise + randomness * rand_noise) / ((randomness**2 + (1-randomness)**2) ** 0.5)

            sigmas = p.sampler.model_wrap.get_sigmas(p.steps)

            noise_dt = combined_noise - (p.init_latent / sigmas[0])

            p.seed = p.seed + 1

            return p.sampler.sample_img2img(p, p.init_latent, noise_dt, conditioning, unconditional_conditioning)

        p.sample = sample_extra

        p.extra_generation_params["Decode prompt"] = original_prompt
        p.extra_generation_params["Decode negative prompt"] = original_negative_prompt
        p.extra_generation_params["Decode CFG scale"] = cfg
        p.extra_generation_params["Decode steps"] = st
        p.extra_generation_params["Randomness"] = randomness
        p.extra_generation_params["Sigma Adjustment"] = sigma_adjustment

        processed = processing.process_job(p)

        return processed

