import math
import os.path
from abc import ABC
from typing import Any, Callable, Dict, List, Optional, Union, Set
from openrlhf.models.loss import get_positive_weights_detached, get_normalized_positive_weights_detached

import ray
import torch
import torch.nn as nn
from torch import Tensor
from torch.optim import Optimizer
from torch.utils.data import DataLoader
from tqdm import tqdm
from torch.profiler import profile, record_function, ProfilerActivity

import torch.nn.functional as F


from openrlhf.models import Actor, GPTLMLoss, PolicyLoss, ValueLoss
from openrlhf.models.loss import REINFORCELoss, NegTrainingLoss, NegREINFORCELoss
from openrlhf.models.utils import masked_mean, compute_approx_kl
from openrlhf.utils.distributed_sampler import DistributedSampler
from openrlhf.utils.utils import get_info_name_str, tile_prompts, inspect_rewards_list

from openrlhf.trainer.ppo_utils import AdaptiveKLController, Experience, FixedKLController, NaiveReplayBuffer
from openrlhf.trainer.ppo_utils.experience_maker import BaseExperienceMaker


class HarmlessnessTrainer(ABC):
    """
        Trainer for Harmlessness training algorithm.

    Args:
        TODO THIS DOCUMENTATION NOT UPDATED
        strategy (Strategy): the strategy to use for training
        base_actor (Actor): the actor model in ppo algorithm
        critic (nn.Module): the critic model in ppo algorithm
        reward_model (nn.Module): the reward model in rlhf algorithm to make reward of sentences
        initial_model (Actor): the initial model in rlhf algorithm to generate reference logits to limit the update of actor
        actor_optim (Optimizer): the optimizer to use for actor model
        critic_optim (Optimizer): the optimizer to use for critic model
        kl_coef (float, defaults to 0.1): the coefficient of kl divergence loss
        train_batch_size (int, defaults to 8): the batch size to use for training
        buffer_limit (int, defaults to 0): the max_size limitaiton of replay buffer
        buffer_cpu_offload (bool, defaults to True): whether to offload replay buffer to cpu
        eps_clip (float, defaults to 0.2): the clip coefficient of policy loss
        value_clip (float, defaults to 0.4): the clip coefficient of value loss
        experience_batch_size (int, defaults to 8): the batch size to use for experience generation
        max_epochs (int, defaults to 1): the number of epochs of training process
        tokenier (Callable, optional): the tokenizer to use for tokenizing the input
        sample_replay_buffer (bool, defaults to False): whether to sample from replay buffer
        dataloader_pin_memory (bool, defaults to True): whether to pin memory for data loader
        callbacks (List[Callback], defaults to []): the callbacks to call during training process
        generate_kwargs (dict, optional): the kwargs to use while model generating
        remote_rm_url (str, optional): function for reward model api
    """

    def __init__(
        self,
        strategy,
        base_actor: Actor,
        sampling_actor: Actor,
        critic: nn.Module,
        reward_model: nn.Module,
        initial_model: Actor,
        ema_model: Actor,
        actor_optim: Optimizer,
        critic_optim: Optimizer,
        actor_scheduler,
        critic_scheduler,
        ema_beta: float = 0.992,
        init_kl_coef: float = 0.001,
        kl_target: float = None,
        kl_horizon: int = 10000,
        ptx_coef: float = 0,
        micro_train_batch_size: int = 8,
        buffer_limit: int = 0,
        buffer_cpu_offload: bool = True,
        eps_clip: float = 0.2,
        value_clip: float = 0.2,
        micro_rollout_batch_size: int = 8,
        gradient_checkpointing: bool = False,
        max_epochs: int = 1,
        max_norm: float = 1.0,
        tokenizer: Optional[Callable[[Any], dict]] = None,
        prompt_max_len: int = 128,
        dataloader_pin_memory: bool = True,
        remote_rm_url: str = None,
        reward_fn: Callable[[List[torch.Tensor]], torch.Tensor] = None,
        shared_actorcritic: bool = False,
        vf_coef: float = 0.1,
        model_eval: bool = False,
        threshold: float = -5.,
        reward_cap: float = 4.5,
        target_dist_beta: float = 1,
        n_seeds_f_q: int = 4,
        rm_type: str = '',
        bc_coef: float = 0,
        bc_steps: int = -1,
        true_posterior_samples = None, # would otherwise be torch.Tensor
        actor_loss_type: str = 'reinforce',
        critic_loss_type: str = 'mse',
        alpha: float = 0.5,
        parameterization: str = '',
        save_negdata=False,
        save_negdata_threshold=-10000,
        neg_data: Optional[Set[str]] = None,
        baseline_type: Optional[str] = None,
        hardcoded_baseline: Optional[float] = None,
        baseline_type_neg: Optional[str] = None,
        hardcoded_baseline_neg: Optional[float] = None,
        reward_transform: Optional[str] = None,
        **generate_kwargs,
    ) -> None:
        assert (
            not isinstance(reward_model, List) or len(reward_model) == 1 or reward_fn is not None
        ), "reward_fn must be specified if using multiple reward models"

        super().__init__()
        self.strategy = strategy
        self.args = strategy.args
        self.micro_rollout_batch_size = micro_rollout_batch_size
        self.max_epochs = max_epochs
        self.tokenizer = tokenizer
        self.generate_kwargs = generate_kwargs
        self.dataloader_pin_memory = dataloader_pin_memory
        self.max_norm = max_norm
        self.ptx_coef = ptx_coef
        self.micro_train_batch_size = micro_train_batch_size
        self.kl_target = kl_target
        self.prompt_max_len = prompt_max_len
        self.ema_beta = ema_beta
        self.gradient_checkpointing = gradient_checkpointing
        self.reward_fn = reward_fn
        self.reward_transform = reward_transform

        self.neg_data = neg_data

        self.base_actor = base_actor
        self.critic = critic
        self.reward_model = reward_model
        self.remote_rm_url = remote_rm_url
        self.initial_model = initial_model
        self.ema_model = ema_model
        self.actor_optim = actor_optim
        self.critic_optim = critic_optim
        self.actor_scheduler = actor_scheduler
        self.critic_scheduler = critic_scheduler

        assert parameterization != ""
        self.parameterization = parameterization


        # Just do very simple negative training, REINFORCE (on base samples), and REINFORCE (on sigma samples)
        # Then have the ability to combine the above ones (we need REINFORCE on base samples, what is "actor" here, plus with either neg train or reinforce on bad (sigma) samples)

        self.actor_loss_type = actor_loss_type
        if self.actor_loss_type == "reinforce":
            self.actor_loss_fn = REINFORCELoss(baseline_type=baseline_type, hardcoded_baseline=hardcoded_baseline) # PolicyLoss(eps_clip)
        elif self.actor_loss_type == "neg_training":
            self.actor_loss_fn = NegTrainingLoss(alpha=alpha, baseline_type=baseline_type, hardcoded_baseline=hardcoded_baseline)
        elif self.actor_loss_type == "neg_reinforce":
            self.actor_loss_fn = NegREINFORCELoss(
                alpha=alpha, baseline_type=baseline_type, hardcoded_baseline=hardcoded_baseline,
                baseline_type_neg=baseline_type_neg, hardcoded_baseline_neg=hardcoded_baseline_neg,
            )
        else:
            raise NotImplementedError


        self.shuffle_replay_buffer_sample = False


        self.critic_loss_type = critic_loss_type



        self.freezing_actor_steps = getattr(self.args, "freezing_actor_steps", -1)

        self.vf_coef = vf_coef
        self.bc_coef = bc_coef

        self.bc_steps = bc_steps

        self.true_posterior_samples = true_posterior_samples

        self.model_eval = model_eval

        self.n_seeds_f_q = n_seeds_f_q

        # Mixtral 8x7b
        self.aux_loss = self.args.aux_loss_coef > 1e-8

        if self.kl_target:
            self.kl_ctl = AdaptiveKLController(init_kl_coef, kl_target, kl_horizon)
        else:
            self.kl_ctl = FixedKLController(init_kl_coef)

        self.shared_actorcritic = shared_actorcritic

        self.experience_maker = BaseExperienceMaker(
            base_actor,
            None,
            reward_model,
            initial_model,
            tokenizer,
            prompt_max_len,
            self.kl_ctl,
            strategy,
            remote_rm_url,
            reward_fn,
            shared_actorcritic,
            threshold,
            reward_cap,
            1, # target_dist_beta 1 here, because this is just going to need regular rewards for REINFORCE
            alpha,
            rm_type,
            actor_loss_type,
            self.generate_kwargs['max_new_tokens'],
            save_negdata=save_negdata,
            save_negdata_threshold=save_negdata_threshold,
            neg_data=self.neg_data,
            reward_transform = self.reward_transform
        )

        # This one needs SMC (or SIS) sampling from the approx target so we need the target_dist_beta here
        self.experience_maker_neg_sampling = BaseExperienceMaker(
            sampling_actor,
            critic,
            reward_model,
            initial_model,
            tokenizer,
            prompt_max_len,
            self.kl_ctl,
            strategy,
            remote_rm_url,
            reward_fn,
            shared_actorcritic,
            threshold,
            reward_cap,
            target_dist_beta,
            alpha,
            rm_type,
            actor_loss_type,
            self.generate_kwargs['max_new_tokens'],
            save_negdata=save_negdata,
            save_negdata_threshold=save_negdata_threshold,
            neg_data=self.neg_data,
            reward_transform=self.reward_transform
        )
        self.replay_buffer = NaiveReplayBuffer(micro_train_batch_size, buffer_limit, buffer_cpu_offload)
        self.replay_buffer_neg_sampling = NaiveReplayBuffer(micro_train_batch_size, buffer_limit, buffer_cpu_offload)

        from collections import defaultdict
        self.gradient_history = defaultdict(list)

        self._wandb = None
        if self.strategy.args.use_wandb and self.strategy.is_rank_0():
            import wandb

            self._wandb = wandb
            if not wandb.api.api_key:
                wandb.login(key=strategy.args.use_wandb)
            wandb.init(
                entity=strategy.args.wandb_org,
                project=strategy.args.wandb_project,
                group=strategy.args.wandb_group,
                name=strategy.args.wandb_run_name,
                config=strategy.args.__dict__,
                reinit=True,
            )

            wandb.define_metric("train/global_step")
            wandb.define_metric("train/*", step_metric="train/global_step", step_sync=True)
            wandb.define_metric("eval/epoch")
            wandb.define_metric("eval/*", step_metric="eval/epoch", step_sync=True)

        self.total_steps = 0

    def fit(
        self,
        args,
        prompts_dataloader,
        pretrain_dataloader,
        consumed_samples=0,
        num_update_steps_per_episodes=1,
        true_posterior_samples=None,
    ) -> (List, List, List, List):

        if args.custom_single_prompt:
            update_timesteps = 1
            num_rollouts_per_episodes = 1

        else:
            num_rollouts_per_episodes = (
                num_update_steps_per_episodes * args.train_batch_size // args.max_epochs // args.rollout_batch_size
            )
            update_timesteps = args.rollout_batch_size // (self.strategy.world_size * self.micro_rollout_batch_size)

            # print(num_update_steps_per_episodes)
            # print(args.train_batch_size)
            # print(args.max_epochs)
            # print(args.rollout_batch_size)
            # print(num_rollouts_per_episodes)
            # print(self.strategy.world_size)
            # print(self.micro_rollout_batch_size)
            # print(update_timesteps)

        # get eval and save steps
        if args.eval_steps == -1:
            args.eval_steps = num_rollouts_per_episodes  # Evaluate once per epoch
        if args.save_steps_harmless == -1:
            args.save_steps_harmless = float("inf")  # do not save ckpt

        self.prompts_dataloader = prompts_dataloader
        self.pretrain_dataloader = pretrain_dataloader

        # Restore step and start_epoch

        # for param in self.base_actor.model.parameters():
        #     print("PARAM CHECK HARML")
        #     print(param)
        #     break
        #
        # for param in self.experience_maker_neg_sampling.actor.model.parameters():
        #     print("PARAM CHECK HARML SAMPLING ACTOR")
        #     print(param)
        #     break
        #
        # try:
        #     for param in self.experience_maker_neg_sampling.actor.modulation_head.parameters():
        #         print("PARAM CHECK HARML SAMPLING ACTORCUSTOM")
        #         print(param)
        #         break
        # except:
        #     print("error")


        print("INSPECT_HARMLESS")
        print(num_update_steps_per_episodes)
        print(args.train_batch_size)
        print(args.max_epochs)
        print(args.rollout_batch_size)
        print(args.train_batch_size // args.max_epochs // args.rollout_batch_size)

        print(consumed_samples)
        print(args.rollout_batch_size)
        print(num_rollouts_per_episodes)


        steps = consumed_samples // args.rollout_batch_size * update_timesteps + 1
        start_episode = consumed_samples // args.rollout_batch_size // num_rollouts_per_episodes
        consumed_samples = consumed_samples % (num_rollouts_per_episodes * args.rollout_batch_size)

        print("INSPECT_HARMLESS2")
        print(steps)
        print(start_episode)
        print(consumed_samples)

        if consumed_samples > 0:
            raise NotImplementedError # Should check that this all works correctly after I modified it.

        iwae_lbs_list = []
        iwae_ubs_list = []
        f_q_estimates_list = []
        g_q_estimates_list = []
        rewards_list = []
        kl_vals_list = []
        entropy_list = []


        # if true_posterior_samples is not None:
        #     n_seeds_f_q = true_posterior_samples.shape[0] // args.train_batch_size
        #     print(f"n_seeds_f_q: {n_seeds_f_q}")
        # rewards_list = []
        # kl_to_prior_list = []

        estimates_list = (f_q_estimates_list, rewards_list, kl_vals_list, entropy_list)

        custom_prompt = None
        if args.custom_single_prompt:
            raise NotImplementedError # Not yet tested
            # if 'TinyStories' in args.pretrain:
            #     prompt_text = 'Once upon a time, there was a'
            # elif 'gpt2' in args.pretrain:
            #     if args.rm_type == 'toy_rlhf':
            #         prompt_text = "Who is the greatest basketball player of all time?"
            #     else:
            #         raise NotImplementedError
            # else:
            #     raise NotImplementedError
            #
            # custom_prompt = [prompt_text] * args.rollout_batch_size
            # print("USING CUSTOM PROMPT")
            # print(len(custom_prompt))
            # start_episode = 0 # TODO later make sure this hasn't messed things up
            # steps = 0 # TODO later make sure this hasn't messed things up
            #
            #
            #
            # if not args.no_test_info:
            #     self.f_q_g_q_evaluation(args, f_q_estimates_list,
            #                             g_q_estimates_list, iwae_lbs_list,
            #                             iwae_ubs_list, prompt_text,
            #                             true_posterior_samples)
            #
            #
            #
            # for episode in range(start_episode, args.num_episodes):
            #
            #     print(f"Episode: {episode}", flush=True)
            #
            #     if isinstance(self.prompts_dataloader.sampler, DistributedSampler):
            #         self.prompts_dataloader.sampler.set_epoch(
            #             episode, consumed_samples=0 if episode > start_episode else consumed_samples
            #         )
            #     pbar = tqdm(
            #         range(self.prompts_dataloader.__len__()),
            #         desc=f"Episode [{episode + 1}/{args.num_episodes}]",
            #         disable=not self.strategy.is_rank_0(),
            #     )
            #
            #
            #     if steps % update_timesteps == 0:
            #
            #         print(f"Step: {steps}")
            #
            #         global_steps = steps // update_timesteps
            #
            #         if self.bc_steps > 0:
            #             if global_steps >= self.bc_steps:
            #                 self.bc_coef = 0
            #
            #         num_twist_updates_to_do = args.update_steps_per_episode
            #         if args.exp_num_twist_updates:
            #             if episode == 0:
            #                 num_twist_updates_to_do = 2
            #             else:
            #                 num_twist_updates_to_do = 2 ** episode
            #
            #         # print(self.generate_kwargs)
            #         # print(self.generate_kwargs['attention_mask'])
            #         # 1/0
            #
            #         for update in range(num_twist_updates_to_do):
            #             experience = self.experience_maker.make_experience(
            #                 custom_prompt,
            #                 samples_per_prompt=args.duplicate_rollout_batch_by,
            #                 **self.generate_kwargs)
            #
            #             if update == 0:
            #                 # print prompt/answer ONCE per number of updates
            #                 output = self.tokenizer.batch_decode(
            #                     experience.sequences,
            #                     skip_special_tokens=True)
            #                 self.strategy.print(output[0])
            #
            #             self.replay_buffer.append(experience)
            #
            #             torch.cuda.empty_cache()
            #             # print("REPLAY BUFFER BEFORE NORMALIZATION")
            #             # print(self.replay_buffer.items)
            #             self.replay_buffer.normalize("advantages", self.strategy)
            #             # print("REPLAY BUFFER AFTER NORMALIZATION")
            #             # print(self.replay_buffer.items)
            #
            #             status = self.train(global_steps, custom_prompt=custom_prompt)
            #             self.replay_buffer.clear()
            #             torch.cuda.empty_cache()
            #
            #             if "kl" in status:
            #                 self.kl_ctl.update(status["kl"],
            #                                    args.rollout_batch_size)
            #             pbar.set_postfix(status)
            #
            #         steps = steps + 1
            #         global_steps = steps // update_timesteps
            #
            #         # logs/checkpoints
            #         client_states = {
            #             "consumed_samples": global_steps * args.rollout_batch_size}
            #         self.save_logs_and_checkpoints(args, global_steps, pbar,
            #                                        status, client_states)
            #
            #     if not args.no_test_info:
            #         self.f_q_g_q_evaluation(args, f_q_estimates_list,
            #                                 g_q_estimates_list, iwae_lbs_list,
            #                                 iwae_ubs_list, prompt_text,
            #                                 true_posterior_samples)
            #
            #     pbar.update()

        else:
            assert start_episode < args.harmlessness_training_episodes_per_loop # Otherwise no updates done; this might be ok depending on setup, but for now this would be unexpected behaviour.

            for episode in range(start_episode, args.harmlessness_training_episodes_per_loop):
                print(f"HARMLESSNESS TRAINING EPISODE {episode}", flush=True)
                if isinstance(self.prompts_dataloader.sampler, DistributedSampler):
                    self.prompts_dataloader.sampler.set_epoch(
                        episode, consumed_samples=0 if episode > start_episode else consumed_samples
                    )
                pbar = tqdm(
                    range(self.prompts_dataloader.__len__()),
                    desc=f"Episode [{episode + 1}/{args.harmlessness_training_episodes_per_loop}]",
                    disable=not self.strategy.is_rank_0(),
                )

                print("DATALOADER_HARMLESS")
                print(self.prompts_dataloader.sampler, flush=True)
                print(self.prompts_dataloader.__len__(), flush=True)

                for rand_prompts in self.prompts_dataloader:

                    # print("rand_prompts_HARMLESS")
                    # print(rand_prompts, flush=True)

                    # if not args.no_test_info:
                    #     if steps == 1: # do some test at the very beginning
                    #         self.test_info_multiprompt(args, rand_prompts, estimates_list)

                    # with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
                    #              profile_memory=True, record_shapes=True) as prof:

                    experience = self.experience_maker.make_experience(
                        rand_prompts,
                        samples_per_prompt=args.duplicate_rollout_batch_by,
                        **self.generate_kwargs
                    )

                    if self.actor_loss_type == "reinforce":
                        experience_neg = experience # This experience_neg will not be used with reinforce anyway
                    else:
                        experience_neg = self.experience_maker_neg_sampling.make_experience(
                            rand_prompts,
                            samples_per_prompt=args.duplicate_rollout_batch_by,
                            **self.generate_kwargs
                        )

                    # print("PROFILE1")
                    # print(prof.key_averages().table(sort_by="self_cuda_memory_usage"))


                    # print prompt/answer in each update step
                    if steps % update_timesteps == 0:
                        output = self.tokenizer.batch_decode(experience.sequences, skip_special_tokens=True)
                        self.strategy.print(output[0])
                    self.replay_buffer.append(experience)
                    self.replay_buffer_neg_sampling.append(experience_neg)

                    # with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
                    #              profile_memory=True, record_shapes=True) as prof:

                    self.total_steps += 1  # do this update before the save_steps, so that saving does happen e.g. if you do 4 save_steps, then on the 4th step, saving will actually happen
                    # so far I modified self.save_logs_and_checkpoints, this should be the only place using self.total_steps

                    if steps % update_timesteps == 0:
                        global_steps = steps // update_timesteps

                        torch.cuda.empty_cache()
                        self.replay_buffer.normalize(self.strategy, "advantages")
                        self.replay_buffer_neg_sampling.normalize(self.strategy, "advantages")

                        assert custom_prompt is None
                        status = self.train(global_steps, custom_prompt=custom_prompt)
                        self.replay_buffer.clear()
                        self.replay_buffer_neg_sampling.clear()
                        torch.cuda.empty_cache()

                        if "kl" in status:
                            self.kl_ctl.update(status["kl"], args.rollout_batch_size)
                        pbar.set_postfix(status)

                        # logs/checkpoints
                        client_states = {"consumed_samples": global_steps * args.rollout_batch_size}
                        self.save_logs_and_checkpoints(args, global_steps, pbar, status, client_states)

                        # if not args.no_test_info:
                        #     if steps % args.test_info_every == 0:
                        #         self.test_info_multiprompt(args, rand_prompts, estimates_list)

                    # print("PROFILE2")
                    # print(prof.key_averages().table(sort_by="self_cuda_memory_usage"))

                    pbar.update()
                    steps = steps + 1

                    # if args.reward_transform is not None:
                    #     r = self.experience_maker.compute_reward_no_kl(experience.sequences,
                    #                                                         experience.attention_mask,
                    #                                                         force_no_transform=True)
                    #     rewards_list.append(r.mean().item())  # Use the non transformed reward for tracking
                    #     # Right now this is a bit inefficient; requires additional rm pass. Should just return 2 values
                    #     # from the rm, but this requires modifying experience everywhere and the RM calls... so this implementation is easier but computationally inefficient
                    # else:

                    rewards_list.append(experience.info["untransformed_reward"].mean().item())
                    inspect_rewards_list(rewards_list)

        if args.custom_single_prompt:
            return iwae_lbs_list, iwae_ubs_list, f_q_estimates_list, g_q_estimates_list
        else:
            return estimates_list



    def train(self, global_steps=0, custom_prompt=None):
        # replay buffer may be empty at first, we should rebuild at each training
        dataloader = DataLoader(
            self.replay_buffer,
            batch_size=self.replay_buffer.sample_batch_size,
            shuffle=self.shuffle_replay_buffer_sample,
            drop_last=True,
            pin_memory=self.dataloader_pin_memory,
            collate_fn=self.replay_buffer.collate_fn,
        )
        dataloader_neg = DataLoader(
            self.replay_buffer_neg_sampling,
            batch_size=self.replay_buffer_neg_sampling.sample_batch_size,
            shuffle=self.shuffle_replay_buffer_sample,
            drop_last=True,
            pin_memory=self.dataloader_pin_memory,
            collate_fn=self.replay_buffer_neg_sampling.collate_fn,
        )
        device = torch.cuda.current_device()

        status_list = []
        status_mean = {}
        for epoch in range(self.max_epochs):
            assert len(dataloader) == len(dataloader_neg)
            pbar = tqdm(
                zip(dataloader, dataloader_neg),  # Zip both dataloaders
                desc=f"Train epoch [{epoch + 1}/{self.max_epochs}]",
                disable=not self.strategy.is_rank_0(),
                total=min(len(dataloader), len(dataloader_neg))  # Ensure tqdm gets a proper length
            )

            # pbar = tqdm(
            #     dataloader,
            #     desc=f"Train epoch [{epoch + 1}/{self.max_epochs}]",
            #     disable=not self.strategy.is_rank_0(),
            # )
            # for experience in pbar:
            for experience, experience_neg in pbar:
                experience.to_device(device)
                experience_neg.to_device(device)
                status = self.training_step(experience, experience_neg, global_steps, custom_prompt=custom_prompt)

                # for DP
                # weighted mean for kl
                if "kl" in status:
                    status["kl"] *= status["response_length"]
                    status = self.strategy.all_reduce(status)
                    status["kl"] /= status["response_length"]

                short_status = {}

                if "policy_loss" in status:
                    short_status = {
                        "pg": status["policy_loss"],
                        "rm": status["reward"],
                        "ret": status["return"],
                        "glen": status["response_length"],
                        "tlen": status["total_length"],
                        "kl": status["kl"],
                        "act_lr": status["actor_lr"],
                    }
                else:
                    short_status = {
                        "rm": status["reward"],
                        "ret": status["return"],
                        "glen": status["response_length"],
                        "tlen": status["total_length"],
                        "kl": status["kl"],
                        "act_lr": status["actor_lr"],
                    }

                if "critic_loss" in status:
                    short_status["cri"] = status["critic_loss"]
                    short_status["vals"] = status["values"]
                    if "critic_lr" in status:
                        short_status["cri_lr"] = status["critic_lr"]

                if "ptx_loss" in status:
                    short_status["ptx"] = status["ptx_loss"]

                status_list.append(status)
                pbar.set_postfix(short_status)

        if status_list:
            status_mean = status_list[0]
            for m in status_list[1:]:
                for k, v in m.items():
                    status_mean[k] += v
            for k in status_mean.keys():
                status_mean[k] /= len(status_list)
        return status_mean


    def training_step(self, experience: Experience, experience_neg: Experience, global_steps, custom_prompt=None) -> Dict[str, float]:
        status = {}
        if self.shared_actorcritic:
            raise NotImplementedError
        else:
            if global_steps > self.freezing_actor_steps:
                status = self.training_step_actor(experience, experience_neg, custom_prompt=custom_prompt)

            if self.critic is not None:
                raise NotImplementedError
                status.update(self.training_step_critic(experience, custom_prompt=custom_prompt))

        return status

    def training_step_actor(self, experience: Experience, experience_neg: Experience, custom_prompt=None) -> Dict[str, float]:
        if self.model_eval:
            self.base_actor.eval()
        else:
            self.base_actor.train()

        actor_loss = self.get_actor_loss(experience, experience_neg, custom_prompt)

        # mixtral
        if self.aux_loss:
            raise NotImplementedError
            # aux_loss = output.aux_loss
        else:
            aux_loss = 0
        loss = actor_loss + aux_loss * self.args.aux_loss_coef

        if self.bc_coef > 0:
            raise NotImpelementedError
            print("DOING BEHAVIOUR CLONING")

        # print("BASE ACTOR OPTIM 2")
        # print(self.actor_optim)

        # for param in self.base_actor.model.parameters():
        #     print("PARAM CHECK HARML before loss")
        #     print(param)
        #     break

        self.strategy.backward(loss, self.base_actor, self.actor_optim)


        # ptx loss
        if self.pretrain_dataloader is not None:
            raise NotImplementedError # not yet checked/fixed
            data = next(self.pretrain_dataloader)
            inputs = data[1].squeeze(1).to(torch.cuda.current_device())
            attention_mask = data[2].squeeze(1).to(torch.cuda.current_device())
            label = torch.where(
                attention_mask.bool(),
                inputs,
                self.ptx_loss_fn.IGNORE_INDEX,
            )

            output = self.base_actor(inputs, attention_mask=attention_mask, return_output=True)
            ptx_log_probs = output["logits"]

            # loss function
            ptx_loss = self.ptx_loss_fn(ptx_log_probs, label)
            # mixtral
            if self.aux_loss:
                aux_loss = output.aux_loss
            else:
                aux_loss = 0
            loss = ptx_loss + aux_loss * self.args.aux_loss_coef
            self.strategy.backward(self.ptx_coef * loss, self.base_actor, self.actor_optim)


        self.strategy.optimizer_step(self.actor_optim, self.base_actor, self.actor_scheduler, name="actor")
        if self.ema_model:
            self.strategy.moving_average(self.base_actor, self.ema_model, self.ema_beta, "cpu")

        for param in self.base_actor.model.parameters():
            print("PARAM CHECK HARML after loss")
            print(param)
            break

        # status
        status = {"policy_loss": actor_loss.item(), "actor_lr": self.actor_scheduler.get_last_lr()[0]}
        if self.pretrain_dataloader is not None:
            status["ptx_loss"] = ptx_loss.item()
        for k, v in experience.info.items():
            if k == "kl":
                status[k] = (
                    (v * experience.info["response_length"]).sum() / experience.info["response_length"].sum()
                ).item()
            else:
                status[k] = v.mean().item()
        return status

    def get_actor_loss(self, experience: Experience, experience_neg: Experience, custom_prompt=None):

        batch_size = experience.sequences.size(0)
        samples_per_prompt = self.args.duplicate_rollout_batch_by
        num_prompts = batch_size // samples_per_prompt

        # print("inspection 03-29")
        # print(experience.action_mask)
        # print(experience.action_mask.shape)
        # print(experience.action_mask.size(1))
        # print(experience.sequences)
        # print(experience.sequences.shape)

        if self.actor_loss_type == "reinforce":
            action_log_probs = self.base_actor(
                experience.sequences, experience.action_mask.size(1),
                attention_mask=experience.attention_mask, return_output=False
            )

            # print("INSPECT 03-30")
            # print(num_prompts)
            # print(samples_per_prompt)
            # print(action_log_probs.shape)

            action_log_probs = action_log_probs.view(num_prompts, samples_per_prompt, -1)
            final_reward = experience.info["reward"].view(num_prompts, samples_per_prompt).to(action_log_probs.device)
            exper_action_mask = experience.action_mask.view(num_prompts, samples_per_prompt, -1)

            # print(action_log_probs)
            # print(experience.action_log_probs)
            # print(experience.advantages)
            # print(experience.action_mask)

            actor_loss = self.actor_loss_fn(
                action_log_probs,
                final_reward,
                action_mask=exper_action_mask,
            )

        elif self.actor_loss_type == "neg_training":
            action_log_probs = self.base_actor(
                experience.sequences, experience.action_mask.size(1),
                attention_mask=experience.attention_mask, return_output=False
            )

            action_log_probs_neg = self.base_actor(
                experience_neg.sequences, experience_neg.action_mask.size(1),
                attention_mask=experience_neg.attention_mask, return_output=False
            )


            action_log_probs = action_log_probs.view(num_prompts, samples_per_prompt, -1)
            action_log_probs_neg = action_log_probs_neg.view(num_prompts, samples_per_prompt, -1)

            final_reward = experience.info["reward"].view(num_prompts, samples_per_prompt).to(action_log_probs.device)
            final_reward_neg = experience_neg.info["reward"].view(num_prompts, samples_per_prompt).to(action_log_probs_neg.device)

            exper_action_mask = experience.action_mask.view(num_prompts, samples_per_prompt, -1)
            exper_neg_action_mask = experience_neg.action_mask.view(num_prompts, samples_per_prompt, -1)

            # print("SHAPES")
            # print(action_log_probs_neg.shape)
            # print(experience_neg.action_log_probs.shape)
            # print(experience_neg.action_log_probs.view(num_prompts, samples_per_prompt, -1).shape)
            # print(experience_neg.returns.shape)
            # print(experience_neg.returns.view(num_prompts, samples_per_prompt, -1).shape)
            # print(experience_neg.returns.view(num_prompts, samples_per_prompt, -1)[:, :, -1].shape)
            # print(experience_neg.returns)
            # print(experience_neg.returns.view(num_prompts, samples_per_prompt, -1)[:, :, -1])

            normalized_w_t_approx_sigma_samples = get_normalized_positive_weights_detached(
                action_log_probs_neg,
                experience_neg.action_log_probs.view(num_prompts, samples_per_prompt, -1),
                final_reward_neg
            )

            actor_loss = self.actor_loss_fn(
                action_log_probs,
                action_log_probs_neg,
                final_reward,
                normalized_w_t_approx_sigma_samples=normalized_w_t_approx_sigma_samples, # TODO fill in with maybe the log p phi / q calculation. p has to be using what, using the base_actor I guess, whereas q is the proposal or sampling actor now.
                action_mask=exper_action_mask,
                action_mask_neg=exper_neg_action_mask,
            )
        elif self.actor_loss_type == "neg_reinforce":
            action_log_probs = self.base_actor(
                experience.sequences, experience.action_mask.size(1),
                attention_mask=experience.attention_mask, return_output=False
            )

            action_log_probs_neg = self.base_actor(
                experience_neg.sequences, experience_neg.action_mask.size(1),
                attention_mask=experience_neg.attention_mask, return_output=False
            )


            action_log_probs = action_log_probs.view(num_prompts, samples_per_prompt, -1)
            action_log_probs_neg = action_log_probs_neg.view(num_prompts, samples_per_prompt, -1)

            final_reward = experience.info["reward"].view(num_prompts, samples_per_prompt).to(action_log_probs.device)
            final_reward_neg = experience_neg.info["reward"].view(num_prompts, samples_per_prompt).to(action_log_probs_neg.device)

            exper_action_mask = experience.action_mask.view(num_prompts, samples_per_prompt, -1)
            exper_neg_action_mask = experience_neg.action_mask.view(num_prompts, samples_per_prompt, -1)

            normalized_w_t_approx_sigma_samples = get_normalized_positive_weights_detached(
                action_log_probs_neg,
                experience_neg.action_log_probs.view(num_prompts, samples_per_prompt, -1),
                final_reward_neg
            )

            # TODO fill out all the arguments below correctly, check each one
            actor_loss = self.actor_loss_fn(
                action_log_probs,
                action_log_probs_neg,
                final_reward,
                final_reward_neg,
                normalized_w_t_approx_sigma_samples=normalized_w_t_approx_sigma_samples, # TODO fill in with maybe the log p phi / q calculation. p has to be using what, using the base_actor I guess, whereas q is the proposal or sampling actor now.
                action_mask=exper_action_mask,
                action_mask_neg=exper_neg_action_mask,
            )

        else:
            raise NotImplementedError

        return actor_loss


    def training_step_critic(self, experience: Experience, custom_prompt=None) -> Dict[str, float]:
        raise NotImplementedError # Not yet tested
        if self.model_eval:
            self.critic.eval()
        else:
            self.critic.train()

        # critic loss
        values, output = self.critic(
            experience.sequences,
            action_mask=experience.action_mask,
            attention_mask=experience.attention_mask,
            return_output=True,
        )
        # loss function
        critic_loss = self.get_critic_loss(experience, values, custom_prompt=custom_prompt)
        # mixtral
        if self.aux_loss:
            aux_loss = output.aux_loss
        else:
            aux_loss = 0
        loss = critic_loss + aux_loss * self.args.aux_loss_coef
        loss = loss.float()
        self.strategy.backward(loss, self.critic, self.critic_optim)
        self.strategy.optimizer_step(self.critic_optim, self.critic, self.critic_scheduler, name="critic")

        # status
        status = {
            "critic_loss": critic_loss.item(),
            "values": masked_mean(values, experience.action_mask).item(),
            "critic_lr": self.critic_scheduler.get_last_lr()[0],
        }
        return status

    def get_critic_loss(self, experience, values, custom_prompt=None):
        raise NotImplementedError # not yet tested


    def save_logs_and_checkpoints(self, args, global_step, step_bar, logs_dict={}, client_states={}):
        if global_step % args.logging_steps == 0:
            # wandb
            if self._wandb is not None and self.strategy.is_rank_0():
                logs = {
                    "train/%s" % k: v
                    for k, v in {
                        **logs_dict,
                        "global_step": global_step,
                    }.items()
                }
                self._wandb.log(logs)

        # TODO: Add evaluation mechanism for PPO
        if global_step % args.eval_steps == 0:
            # self.evaluate(self.eval_dataloader, global_step)
            pass
        # save ckpt
        # TODO: save best model on dev, use loss/perplexity/others on whole dev dataset as metric

        if self.total_steps > 0 and self.total_steps % args.save_steps_harmless == 0:
        # if global_step % args.save_steps == 0:
            print(f"SAVING CHECKPOINT AT TOTAL POLICY HARMLESSNESS TRAINING STEPs {self.total_steps}", flush=True)
            tag = f"total_step{self.total_steps}"
            self._save_checkpoint(args, tag, client_states)


    def _save_checkpoint(self, args, tag, client_states):

        info_name_str = get_info_name_str(args)
        save_str = f"{info_name_str}"
        # save_str = f"PPOepochs{args.max_epochs}{eval_str}_lrschedule{args.lr_scheduler}_{lr_str}_criticloss{args.critic_loss_type}_{extra_str}_seed{args.seed}"

        self.strategy.save_ckpt(
            self.base_actor.model,
            os.path.join(args.ckpt_path, f"{save_str}_harml_actor"),
            tag,
            args.max_ckpt_num,
            args.max_ckpt_mem,
            client_states,
        )
        if self.critic is not None:
            if not args.no_save_critic:
                self.strategy.save_ckpt(
                    self.critic, os.path.join(args.ckpt_path, f"{save_str}_harml_critic"), tag, args.max_ckpt_num, args.max_ckpt_mem
                )
