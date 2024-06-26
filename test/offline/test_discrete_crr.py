import argparse
import os
import pickle
import pprint

import gymnasium as gym
import numpy as np
import torch
from torch.utils.tensorboard import SummaryWriter

from tianshou.data import Collector, VectorReplayBuffer
from tianshou.env import DummyVectorEnv
from tianshou.policy import BasePolicy, DiscreteCRRPolicy
from tianshou.trainer import OfflineTrainer
from tianshou.utils import TensorboardLogger
from tianshou.utils.net.common import ActorCritic, Net
from tianshou.utils.net.discrete import Actor, Critic
from tianshou.utils.space_info import SpaceInfo

if __name__ == "__main__":
    from gather_cartpole_data import expert_file_name, gather_data
else:  # pytest
    from test.offline.gather_cartpole_data import expert_file_name, gather_data


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, default="CartPole-v0")
    parser.add_argument("--reward-threshold", type=float, default=None)
    parser.add_argument("--seed", type=int, default=1626)
    parser.add_argument("--lr", type=float, default=7e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--n-step", type=int, default=3)
    parser.add_argument("--target-update-freq", type=int, default=320)
    parser.add_argument("--epoch", type=int, default=5)
    parser.add_argument("--update-per-epoch", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--hidden-sizes", type=int, nargs="*", default=[64, 64])
    parser.add_argument("--test-num", type=int, default=100)
    parser.add_argument("--logdir", type=str, default="log")
    parser.add_argument("--render", type=float, default=0.0)
    parser.add_argument("--load-buffer-name", type=str, default=expert_file_name())
    parser.add_argument(
        "--device",
        type=str,
        default="cuda" if torch.cuda.is_available() else "cpu",
    )
    return parser.parse_known_args()[0]


def test_discrete_crr(args: argparse.Namespace = get_args()) -> None:
    # envs
    env = gym.make(args.task)
    assert isinstance(env.action_space, gym.spaces.Discrete)
    space_info = SpaceInfo.from_env(env)
    args.state_shape = space_info.observation_info.obs_shape
    args.action_shape = space_info.action_info.action_shape
    if args.reward_threshold is None:
        default_reward_threshold = {"CartPole-v0": 180}
        args.reward_threshold = default_reward_threshold.get(
            args.task,
            env.spec.reward_threshold if env.spec else None,
        )
    test_envs = DummyVectorEnv([lambda: gym.make(args.task) for _ in range(args.test_num)])
    # seed
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    test_envs.seed(args.seed)
    # model
    net = Net(state_shape=args.state_shape, action_shape=args.hidden_sizes[0], device=args.device)
    actor = Actor(
        preprocess_net=net,
        action_shape=args.action_shape,
        hidden_sizes=args.hidden_sizes,
        device=args.device,
        softmax_output=False,
    )
    action_dim = space_info.action_info.action_dim
    critic = Critic(
        net,
        hidden_sizes=args.hidden_sizes,
        last_size=action_dim,
        device=args.device,
    )
    actor_critic = ActorCritic(actor, critic)
    optim = torch.optim.Adam(actor_critic.parameters(), lr=args.lr)

    policy: DiscreteCRRPolicy = DiscreteCRRPolicy(
        actor=actor,
        critic=critic,
        optim=optim,
        action_space=env.action_space,
        discount_factor=args.gamma,
        target_update_freq=args.target_update_freq,
    ).to(args.device)
    # buffer
    if os.path.exists(args.load_buffer_name) and os.path.isfile(args.load_buffer_name):
        if args.load_buffer_name.endswith(".hdf5"):
            buffer = VectorReplayBuffer.load_hdf5(args.load_buffer_name)
        else:
            with open(args.load_buffer_name, "rb") as f:
                buffer = pickle.load(f)
    else:
        buffer = gather_data()

    # collector
    test_collector = Collector(policy, test_envs, exploration_noise=True)

    log_path = os.path.join(args.logdir, args.task, "discrete_crr")
    writer = SummaryWriter(log_path)
    logger = TensorboardLogger(writer)

    def save_best_fn(policy: BasePolicy) -> None:
        torch.save(policy.state_dict(), os.path.join(log_path, "policy.pth"))

    def stop_fn(mean_rewards: float) -> bool:
        return mean_rewards >= args.reward_threshold

    result = OfflineTrainer(
        policy=policy,
        buffer=buffer,
        test_collector=test_collector,
        max_epoch=args.epoch,
        step_per_epoch=args.update_per_epoch,
        episode_per_test=args.test_num,
        batch_size=args.batch_size,
        stop_fn=stop_fn,
        save_best_fn=save_best_fn,
        logger=logger,
    ).run()

    assert stop_fn(result.best_reward)

    if __name__ == "__main__":
        pprint.pprint(result)
        # Let's watch its performance!
        env = gym.make(args.task)
        policy.eval()
        collector = Collector(policy, env)
        collector_stats = collector.collect(n_episode=1, render=args.render)
        print(collector_stats)


if __name__ == "__main__":
    test_discrete_crr(get_args())
