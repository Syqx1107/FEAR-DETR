# Copyright (c) 2022 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import copy
import time
import typing
import numpy as np

import paddle
import paddle.nn as nn
import paddle.distributed as dist
from paddle.distributed import fleet
from ppdet.optimizer import ModelEMA, SimpleModelEMA
from ppdet.core.workspace import create
from ppdet.utils.checkpoint import load_weight, load_pretrain_weight, save_model
import ppdet.utils.stats as stats
from ppdet.utils import profiler
from ppdet.modeling.ssod.utils import align_weak_strong_shape
from .trainer import Trainer
from ppdet.utils.logger import setup_logger
from paddle.static import InputSpec
from ppdet.engine.export_utils import _dump_infer_config, _prune_input_spec
MOT_ARCH = ['JDE', 'FairMOT', 'DeepSORT', 'ByteTrack', 'CenterTrack']

logger = setup_logger('ppdet.engine')

__all__ = ['Trainer_DenseTeacher', 'Trainer_ARSL']


class Trainer_DenseTeacher(Trainer):
    def __init__(self, cfg, mode='train'):
        self.cfg = cfg
        assert mode.lower() in ['train', 'eval', 'test'], \
                "mode should be 'train', 'eval' or 'test'"
        self.mode = mode.lower()
        self.optimizer = None
        self.is_loaded_weights = False
        self.use_amp = self.cfg.get('amp', False)
        self.amp_level = self.cfg.get('amp_level', 'O1')
        self.custom_white_list = self.cfg.get('custom_white_list', None)
        self.custom_black_list = self.cfg.get('custom_black_list', None)

        # build data loader
        capital_mode = self.mode.capitalize()
        self.dataset = self.cfg['{}Dataset'.format(capital_mode)] = create(
            '{}Dataset'.format(capital_mode))()

        if self.mode == 'train':
            self.dataset_unlabel = self.cfg['UnsupTrainDataset'] = create(
                'UnsupTrainDataset')
            self.loader = create('SemiTrainReader')(
                self.dataset, self.dataset_unlabel, cfg.worker_num)

        # build model
        if 'model' not in self.cfg:
            self.model = create(cfg.architecture)
        else:
            self.model = self.cfg.model
            self.is_loaded_weights = True

        # EvalDataset build with BatchSampler to evaluate in single device
        # TODO: multi-device evaluate
        if self.mode == 'eval':
            self._eval_batch_sampler = paddle.io.BatchSampler(
                self.dataset, batch_size=self.cfg.EvalReader['batch_size'])
            # If metric is VOC, need to be set collate_batch=False.
            if cfg.metric == 'VOC':
                cfg['EvalReader']['collate_batch'] = False
            self.loader = create('EvalReader')(self.dataset, cfg.worker_num,
                                               self._eval_batch_sampler)
        # TestDataset build after user set images, skip loader creation here

        # build optimizer in train mode
        if self.mode == 'train':
            steps_per_epoch = len(self.loader)
            if steps_per_epoch < 1:
                logger.warning(
                    "Samples in dataset are less than batch_size, please set smaller batch_size in TrainReader."
                )
            self.lr = create('LearningRate')(steps_per_epoch)
            self.optimizer = create('OptimizerBuilder')(self.lr, self.model)

            # Unstructured pruner is only enabled in the train mode.
            if self.cfg.get('unstructured_prune'):
                self.pruner = create('UnstructuredPruner')(self.model,
                                                           steps_per_epoch)
        if self.use_amp and self.amp_level == 'O2':
            self.model, self.optimizer = paddle.amp.decorate(
                models=self.model,
                optimizers=self.optimizer,
                level=self.amp_level)

        self.use_ema = ('use_ema' in cfg and cfg['use_ema'])
        if self.use_ema:
            ema_decay = self.cfg.get('ema_decay', 0.9998)
            ema_decay_type = self.cfg.get('ema_decay_type', 'threshold')
            cycle_epoch = self.cfg.get('cycle_epoch', -1)
            ema_black_list = self.cfg.get('ema_black_list', None)
            self.ema = ModelEMA(
                self.model,
                decay=ema_decay,
                ema_decay_type=ema_decay_type,
                cycle_epoch=cycle_epoch,
                ema_black_list=ema_black_list)
            self.ema_start_iters = self.cfg.get('ema_start_iters', 0)

        # simple_ema for SSOD
        self.use_simple_ema = ('use_simple_ema' in cfg and
                               cfg['use_simple_ema'])
        if self.use_simple_ema:
            self.use_ema = True
            ema_decay = self.cfg.get('ema_decay', 0.9996)
            self.ema = SimpleModelEMA(self.model, decay=ema_decay)
            self.ema_start_iters = self.cfg.get('ema_start_iters', 0)

        self._nranks = dist.get_world_size()
        self._local_rank = dist.get_rank()

        self.status = {}

        self.start_epoch = 0
        self.end_epoch = 0 if 'epoch' not in cfg else cfg.epoch

        # initial default callbacks
        self._init_callbacks()

        # initial default metrics
        self._init_metrics()
        self._reset_metrics()

    def load_weights(self, weights):
        if self.is_loaded_weights:
            return
        self.start_epoch = 0
        load_pretrain_weight(self.model, weights)
        load_pretrain_weight(self.ema.model, weights)
        logger.info("Load weights {} to start training for teacher and student".
                    format(weights))

    def resume_weights(self, weights, exchange=True):
        # support Distill resume weights
        if hasattr(self.model, 'student_model'):
            self.start_epoch = load_weight(self.model.student_model, weights,
                                           self.optimizer, exchange)
        else:
            self.start_epoch = load_weight(self.model, weights, self.optimizer,
                                           self.ema
                                           if self.use_ema else None, exchange)
        logger.debug("Resume weights of epoch {}".format(self.start_epoch))

    def train(self, validate=False):
        self.semi_start_iters = self.cfg.get('semi_start_iters', 5000)
        Init_mark = False
        if validate:
            self.cfg['EvalDataset'] = self.cfg.EvalDataset = create(
                "EvalDataset")()

        sync_bn = (getattr(self.cfg, 'norm_type', None) == 'sync_bn' and
                   self.cfg.use_gpu and self._nranks > 1)
        if sync_bn:
            self.model = paddle.nn.SyncBatchNorm.convert_sync_batchnorm(
                self.model)

        if self.cfg.get('fleet', False):
            self.model = fleet.distributed_model(self.model)
            self.optimizer = fleet.distributed_optimizer(self.optimizer)
        elif self._nranks > 1:
            find_unused_parameters = self.cfg[
                'find_unused_parameters'] if 'find_unused_parameters' in self.cfg else False
            self.model = paddle.DataParallel(
                self.model, find_unused_parameters=find_unused_parameters)
            self.ema.model = paddle.DataParallel(
                self.ema.model, find_unused_parameters=find_unused_parameters)

        self.status.update({
            'epoch_id': self.start_epoch,
            'step_id': 0,
            'steps_per_epoch': len(self.loader),
            'exchange_save_model': True,
        })
        # Note: exchange_save_model
        # in DenseTeacher SSOD, the teacher model will be higher, so exchange when saving pdparams

        self.status['batch_time'] = stats.SmoothedValue(
            self.cfg.log_iter, fmt='{avg:.4f}')
        self.status['data_time'] = stats.SmoothedValue(
            self.cfg.log_iter, fmt='{avg:.4f}')
        self.status['training_staus'] = stats.TrainingStats(self.cfg.log_iter)
        profiler_options = self.cfg.get('profiler_options', None)
        self._compose_callback.on_train_begin(self.status)

        train_cfg = self.cfg.DenseTeacher['train_cfg']
        concat_sup_data = train_cfg.get('concat_sup_data', True)

        for param in self.ema.model.parameters():
            param.stop_gradient = True

        for epoch_id in range(self.start_epoch, self.cfg.epoch):
            self.status['mode'] = 'train'
            self.status['epoch_id'] = epoch_id
            self._compose_callback.on_epoch_begin(self.status)
            self.loader.dataset_label.set_epoch(epoch_id)
            self.loader.dataset_unlabel.set_epoch(epoch_id)
            iter_tic = time.time()
            loss_dict = {
                'loss': paddle.to_tensor([0]),
                'loss_sup_sum': paddle.to_tensor([0]),
                'loss_unsup_sum': paddle.to_tensor([0]),
                'fg_sum': paddle.to_tensor([0]),
            }
            if self._nranks > 1:
                for k in self.model._layers.get_loss_keys():
                    loss_dict.update({k: paddle.to_tensor([0.])})
                for k in self.model._layers.get_loss_keys():
                    loss_dict.update({'distill_' + k: paddle.to_tensor([0.])})
            else:
                for k in self.model.get_loss_keys():
                    loss_dict.update({k: paddle.to_tensor([0.])})
                for k in self.model.get_loss_keys():
                    loss_dict.update({'distill_' + k: paddle.to_tensor([0.])})

            # Note: for step_id, data in enumerate(self.loader): # enumerate bug
            for step_id in range(len(self.loader)):
                data = next(self.loader)

                self.model.train()
                self.ema.model.eval()
                data_sup_w, data_sup_s, data_unsup_w, data_unsup_s = data

                self.status['data_time'].update(time.time() - iter_tic)
                self.status['step_id'] = step_id
                profiler.add_profiler_step(profiler_options)
                self._compose_callback.on_step_begin(self.status)

                if data_sup_w['image'].shape != data_sup_s['image'].shape:
                    data_sup_w, data_sup_s = align_weak_strong_shape(data_sup_w,
                                                                     data_sup_s)

                data_sup_w['epoch_id'] = epoch_id
                data_sup_s['epoch_id'] = epoch_id
                if concat_sup_data:
                    for k, v in data_sup_s.items():
                        if k in ['epoch_id']:
                            continue
                        data_sup_s[k] = paddle.concat([v, data_sup_w[k]])
                    loss_dict_sup = self.model(data_sup_s)
                else:
                    loss_dict_sup_w = self.model(data_sup_w)
                    loss_dict_sup = self.model(data_sup_s)
                    for k, v in loss_dict_sup_w.items():
                        loss_dict_sup[k] = (loss_dict_sup[k] + v) * 0.5

                losses_sup = loss_dict_sup['loss'] * train_cfg['sup_weight']
                losses_sup.backward()

                losses = losses_sup.detach()
                loss_dict.update(loss_dict_sup)
                loss_dict.update({'loss_sup_sum': loss_dict['loss']})

                curr_iter = len(self.loader) * epoch_id + step_id
                st_iter = self.semi_start_iters
                if curr_iter == st_iter:
                    logger.info("***" * 30)
                    logger.info('Semi starting ...')
                    logger.info("***" * 30)
                if curr_iter > st_iter:
                    unsup_weight = train_cfg['unsup_weight']
                    if train_cfg['suppress'] == 'linear':
                        tar_iter = st_iter * 2
                        if curr_iter <= tar_iter:
                            unsup_weight *= (curr_iter - st_iter) / st_iter
                    elif train_cfg['suppress'] == 'exp':
                        tar_iter = st_iter + 2000
                        if curr_iter <= tar_iter:
                            scale = np.exp((curr_iter - tar_iter) / 1000)
                            unsup_weight *= scale
                    elif train_cfg['suppress'] == 'step':
                        tar_iter = st_iter * 2
                        if curr_iter <= tar_iter:
                            unsup_weight *= 0.25
                    else:
                        raise ValueError

                    if data_unsup_w['image'].shape != data_unsup_s[
                            'image'].shape:
                        data_unsup_w, data_unsup_s = align_weak_strong_shape(
                            data_unsup_w, data_unsup_s)

                    data_unsup_w['epoch_id'] = epoch_id
                    data_unsup_s['epoch_id'] = epoch_id

                    data_unsup_s['get_data'] = True
                    student_preds = self.model(data_unsup_s)

                    with paddle.no_grad():
                        data_unsup_w['is_teacher'] = True
                        teacher_preds = self.ema.model(data_unsup_w)

                    train_cfg['curr_iter'] = curr_iter
                    train_cfg['st_iter'] = st_iter
                    if self._nranks > 1:
                        loss_dict_unsup = self.model._layers.get_ssod_loss(
                            student_preds, teacher_preds, train_cfg)
                    else:
                        loss_dict_unsup = self.model.get_ssod_loss(
                            student_preds, teacher_preds, train_cfg)

                    fg_num = loss_dict_unsup["fg_sum"]
                    del loss_dict_unsup["fg_sum"]
                    distill_weights = train_cfg['loss_weight']
                    loss_dict_unsup = {
                        k: v * distill_weights[k]
                        for k, v in loss_dict_unsup.items()
                    }

                    losses_unsup = sum([
                        metrics_value
                        for metrics_value in loss_dict_unsup.values()
                    ]) * unsup_weight
                    losses_unsup.backward()

                    loss_dict.update(loss_dict_unsup)
                    loss_dict.update({'loss_unsup_sum': losses_unsup})
                    losses += losses_unsup.detach()
                    loss_dict.update({"fg_sum": fg_num})
                    loss_dict['loss'] = losses

                self.optimizer.step()
                curr_lr = self.optimizer.get_lr()
                self.lr.step()
                self.optimizer.clear_grad()
                self.status['learning_rate'] = curr_lr
                if self._nranks < 2 or self._local_rank == 0:
                    self.status['training_staus'].update(loss_dict)

                self.status['batch_time'].update(time.time() - iter_tic)
                self._compose_callback.on_step_end(self.status)
                # Note: ema_start_iters
                if self.use_ema and curr_iter == self.ema_start_iters:
                    logger.info("***" * 30)
                    logger.info('EMA starting ...')
                    logger.info("***" * 30)
                    self.ema.update(self.model, decay=0)
                elif self.use_ema and curr_iter > self.ema_start_iters:
                    self.ema.update(self.model)
                iter_tic = time.time()

            is_snapshot = (self._nranks < 2 or self._local_rank == 0) \
                       and ((epoch_id + 1) % self.cfg.snapshot_epoch == 0 or epoch_id == self.end_epoch - 1)
            if is_snapshot and self.use_ema:
                # apply ema weight on model
                weight = copy.deepcopy(self.ema.model.state_dict())
                for k, v in weight.items():
                    if paddle.is_floating_point(v):
                        weight[k].stop_gradient = True
                self.status['weight'] = weight

            self._compose_callback.on_epoch_end(self.status)

            if validate and is_snapshot:
                if not hasattr(self, '_eval_loader'):
                    # build evaluation dataset and loader
                    self._eval_dataset = self.cfg.EvalDataset
                    self._eval_batch_sampler = \
                        paddle.io.BatchSampler(
                            self._eval_dataset,
                            batch_size=self.cfg.EvalReader['batch_size'])
                    # If metric is VOC, need to be set collate_batch=False.
                    if self.cfg.metric == 'VOC':
                        self.cfg['EvalReader']['collate_batch'] = False
                    self._eval_loader = create('EvalReader')(
                        self._eval_dataset,
                        self.cfg.worker_num,
                        batch_sampler=self._eval_batch_sampler)
                # if validation in training is enabled, metrics should be re-init
                # Init_mark makes sure this code will only execute once
                if validate and Init_mark == False:
                    Init_mark = True
                    self._init_metrics(validate=validate)
                    self._reset_metrics()

                with paddle.no_grad():
                    self.status['save_best_model'] = True
                    self._eval_with_loader(self._eval_loader)

            if is_snapshot and self.use_ema:
                self.status.pop('weight')

        self._compose_callback.on_train_end(self.status)

    def evaluate(self):
        # get distributed model
        if self.cfg.get('fleet', False):
            self.model = fleet.distributed_model(self.model)
            self.optimizer = fleet.distributed_optimizer(self.optimizer)
        elif self._nranks > 1:
            find_unused_parameters = self.cfg[
                'find_unused_parameters'] if 'find_unused_parameters' in self.cfg else False
            self.model = paddle.DataParallel(
                self.model, find_unused_parameters=find_unused_parameters)
        with paddle.no_grad():
            self._eval_with_loader(self.loader)

    def _eval_with_loader(self, loader):
        sample_num = 0
        tic = time.time()
        self._compose_callback.on_epoch_begin(self.status)
        self.status['mode'] = 'eval'

        test_cfg = self.cfg.DenseTeacher['test_cfg']
        if test_cfg['inference_on'] == 'teacher':
            logger.info("***** teacher model evaluating *****")
            eval_model = self.ema.model
        else:
            logger.info("***** student model evaluating *****")
            eval_model = self.model

        eval_model.eval()
        if self.cfg.get('print_flops', False):
            flops_loader = create('{}Reader'.format(self.mode.capitalize()))(
                self.dataset, self.cfg.worker_num, self._eval_batch_sampler)
            self._flops(flops_loader)
        for step_id, data in enumerate(loader):
            self.status['step_id'] = step_id
            self._compose_callback.on_step_begin(self.status)
            # forward
            if self.use_amp:
                with paddle.amp.auto_cast(
                        enable=self.cfg.use_gpu or self.cfg.use_mlu,
                        custom_white_list=self.custom_white_list,
                        custom_black_list=self.custom_black_list,
                        level=self.amp_level):
                    outs = eval_model(data)
            else:
                outs = eval_model(data)

            # update metrics
            for metric in self._metrics:
                metric.update(data, outs)

            # multi-scale inputs: all inputs have same im_id
            if isinstance(data, typing.Sequence):
                sample_num += data[0]['im_id'].numpy().shape[0]
            else:
                sample_num += data['im_id'].numpy().shape[0]
            self._compose_callback.on_step_end(self.status)

        self.status['sample_num'] = sample_num
        self.status['cost_time'] = time.time() - tic

        # accumulate metric to log out
        for metric in self._metrics:
            metric.accumulate()
            metric.log()
        self._compose_callback.on_epoch_end(self.status)
        self._reset_metrics()


class Trainer_ARSL(Trainer):
    def __init__(self, cfg, mode='train'):
        self.cfg = cfg
        assert mode.lower() in ['train', 'eval', 'test'], \
                "mode should be 'train', 'eval' or 'test'"
        self.mode = mode.lower()
        self.optimizer = None
        self.is_loaded_weights = False
        capital_mode = self.mode.capitalize()
        self.use_ema = False
        self.dataset = self.cfg['{}Dataset'.format(capital_mode)] = create(
            '{}Dataset'.format(capital_mode))()
        if self.mode == 'train':
            self.dataset_unlabel = self.cfg['UnsupTrainDataset'] = create(
                'UnsupTrainDataset')
            self.loader = create('SemiTrainReader')(
                self.dataset, self.dataset_unlabel, cfg.worker_num)

        # build model
        if 'model' not in self.cfg:
            self.student_model = create(cfg.architecture)
            self.teacher_model = create(cfg.architecture)
            self.model = EnsembleTSModel(self.teacher_model, self.student_model)
        else:
            self.model = self.cfg.model
            self.is_loaded_weights = True
        # save path for burn-in model
        self.base_path = cfg.get('weights')
        self.base_path = os.path.dirname(self.base_path)

        # EvalDataset build with BatchSampler to evaluate in single device
        # TODO: multi-device evaluate
        if self.mode == 'eval':
            self._eval_batch_sampler = paddle.io.BatchSampler(
                self.dataset, batch_size=self.cfg.EvalReader['batch_size'])
            self.loader = create('{}Reader'.format(self.mode.capitalize()))(
                self.dataset, cfg.worker_num, self._eval_batch_sampler)
        # TestDataset build after user set images, skip loader creation here

        self.start_epoch = 0
        self.end_epoch = 0 if 'epoch' not in cfg else cfg.epoch
        self.epoch_iter = self.cfg.epoch_iter  # set fixed iter in each epoch to control checkpoint

        # build optimizer in train mode
        if self.mode == 'train':
            steps_per_epoch = self.epoch_iter
            self.lr = create('LearningRate')(steps_per_epoch)
            self.optimizer = create('OptimizerBuilder')(self.lr,
                                                        self.model.modelStudent)

        self._nranks = dist.get_world_size()
        self._local_rank = dist.get_rank()

        self.status = {}

        # initial default callbacks
        self._init_callbacks()

        # initial default metrics
        self._init_metrics()
        self._reset_metrics()
        self.iter = 0

    def resume_weights(self, weights):
        # support Distill resume weights
        if hasattr(self.model, 'student_model'):
            self.start_epoch = load_weight(self.model.student_model, weights,
                                           self.optimizer)
        else:
            self.start_epoch = load_weight(self.model, weights, self.optimizer)
        logger.debug("Resume weights of epoch {}".format(self.start_epoch))

    def train(self, validate=False):
        assert self.mode == 'train', "Model not in 'train' mode"
        Init_mark = False

        # if validation in training is enabled, metrics should be re-init
        if validate:
            self._init_metrics(validate=validate)
            self._reset_metrics()

        if self.cfg.get('fleet', False):
            self.model.modelStudent = fleet.distributed_model(
                self.model.modelStudent)
            self.optimizer = fleet.distributed_optimizer(self.optimizer)
        elif self._nranks > 1:
            find_unused_parameters = self.cfg[
                'find_unused_parameters'] if 'find_unused_parameters' in self.cfg else False
            self.model.modelStudent = paddle.DataParallel(
                self.model.modelStudent,
                find_unused_parameters=find_unused_parameters)

        # set fixed iter in each epoch to control checkpoint
        self.status.update({
            'epoch_id': self.start_epoch,
            'step_id': 0,
            'steps_per_epoch': self.epoch_iter
        })
        print('338 Len of DataLoader: {}'.format(len(self.loader)))

        self.status['batch_time'] = stats.SmoothedValue(
            self.cfg.log_iter, fmt='{avg:.4f}')
        self.status['data_time'] = stats.SmoothedValue(
            self.cfg.log_iter, fmt='{avg:.4f}')
        self.status['training_staus'] = stats.TrainingStats(self.cfg.log_iter)

        self._compose_callback.on_train_begin(self.status)

        epoch_id = self.start_epoch
        self.iter = self.start_epoch * self.epoch_iter
        # use iter rather than epoch to control training schedule
        while self.iter < self.cfg.max_iter:
            # epoch loop
            self.status['mode'] = 'train'
            self.status['epoch_id'] = epoch_id
            self._compose_callback.on_epoch_begin(self.status)
            self.loader.dataset_label.set_epoch(epoch_id)
            self.loader.dataset_unlabel.set_epoch(epoch_id)
            paddle.device.cuda.empty_cache()  # clear GPU memory
            # set model status
            self.model.modelStudent.train()
            self.model.modelTeacher.eval()
            iter_tic = time.time()

            # iter loop in each eopch
            for step_id in range(self.epoch_iter):
                data = next(self.loader)
                self.status['data_time'].update(time.time() - iter_tic)
                self.status['step_id'] = step_id
                # profiler.add_profiler_step(profiler_options)
                self._compose_callback.on_step_begin(self.status)

                # model forward and calculate loss
                loss_dict = self.run_step_full_semisup(data)

                if (step_id + 1) % self.cfg.optimize_rate == 0:
                    self.optimizer.step()
                    self.optimizer.clear_grad()
                curr_lr = self.optimizer.get_lr()
                self.lr.step()

                # update log status
                self.status['learning_rate'] = curr_lr
                if self._nranks < 2 or self._local_rank == 0:
                    self.status['training_staus'].update(loss_dict)
                self.status['batch_time'].update(time.time() - iter_tic)
                self._compose_callback.on_step_end(self.status)
                self.iter += 1
                iter_tic = time.time()

            self._compose_callback.on_epoch_end(self.status)

            if validate and (self._nranks < 2 or self._local_rank == 0) \
                    and ((epoch_id + 1) % self.cfg.snapshot_epoch == 0 \
                             or epoch_id == self.end_epoch - 1):
                if not hasattr(self, '_eval_loader'):
                    # build evaluation dataset and loader
                    self._eval_dataset = self.cfg.EvalDataset
                    self._eval_batch_sampler = \
                        paddle.io.BatchSampler(
                            self._eval_dataset,
                            batch_size=self.cfg.EvalReader['batch_size'])
                    self._eval_loader = create('EvalReader')(
                        self._eval_dataset,
                        self.cfg.worker_num,
                        batch_sampler=self._eval_batch_sampler)
                if validate and Init_mark == False:
                    Init_mark = True
                    self._init_metrics(validate=validate)
                    self._reset_metrics()
                with paddle.no_grad():
                    self.status['save_best_model'] = True
                    # before burn-in stage, eval student. after burn-in stage, eval teacher
                    if self.iter <= self.cfg.SEMISUPNET['BURN_UP_STEP']:
                        print("start eval student model")
                        self._eval_with_loader(
                            self._eval_loader, mode="student")
                    else:
                        print("start eval teacher model")
                        self._eval_with_loader(
                            self._eval_loader, mode="teacher")

            epoch_id += 1

        self._compose_callback.on_train_end(self.status)

    def merge_data(self, data1, data2):
        data = copy.deepcopy(data1)
        for k, v in data1.items():
            if type(v) is paddle.Tensor:
                data[k] = paddle.concat(x=[data[k], data2[k]], axis=0)
            elif type(v) is list:
                data[k].extend(data2[k])
        return data

    def run_step_full_semisup(self, data):
        label_data_k, label_data_q, unlabel_data_k, unlabel_data_q = data
        data_merge = self.merge_data(label_data_k, label_data_q)
        loss_sup_dict = self.model.modelStudent(data_merge, branch="supervised")
        loss_dict = {}
        for key in loss_sup_dict.keys():
            if key[:4] == "loss":
                loss_dict[key] = loss_sup_dict[key] * 1
        losses_sup = paddle.add_n(list(loss_dict.values()))
        # norm loss when using gradient accumulation
        losses_sup = losses_sup / self.cfg.optimize_rate
        losses_sup.backward()

        for key in loss_sup_dict.keys():
            loss_dict[key + "_pseudo"] = paddle.to_tensor([0])
        loss_dict["loss_tot"] = losses_sup
        """
        semi-supervised training after burn-in stage
        """
        if self.iter >= self.cfg.SEMISUPNET['BURN_UP_STEP']:
            # init teacher model with burn-up weight
            if self.iter == self.cfg.SEMISUPNET['BURN_UP_STEP']:
                print(
                    'Starting semi-supervised learning and load the teacher model.'
                )
                self._update_teacher_model(keep_rate=0.00)
                # save burn-in model
                if dist.get_world_size() < 2 or dist.get_rank() == 0:
                    print('saving burn-in model.')
                    save_name = 'burnIn'
                    epoch_id = self.iter // self.epoch_iter
                    save_model(self.model, self.optimizer, self.base_path,
                               save_name, epoch_id)
            # Update teacher model with EMA
            elif (self.iter + 1) % self.cfg.optimize_rate == 0:
                self._update_teacher_model(
                    keep_rate=self.cfg.SEMISUPNET['EMA_KEEP_RATE'])

            #warm-up weight for pseudo loss
            pseudo_weight = self.cfg.SEMISUPNET['UNSUP_LOSS_WEIGHT']
            pseudo_warmup_iter = self.cfg.SEMISUPNET['PSEUDO_WARM_UP_STEPS']
            temp = self.iter - self.cfg.SEMISUPNET['BURN_UP_STEP']
            if temp <= pseudo_warmup_iter:
                pseudo_weight *= (temp / pseudo_warmup_iter)

            # get teacher predictions on weak-augmented unlabeled data
            with paddle.no_grad():
                teacher_pred = self.model.modelTeacher(
                    unlabel_data_k, branch='semi_supervised')

            # calculate unsupervised loss on strong-augmented unlabeled data
            loss_unsup_dict = self.model.modelStudent(
                unlabel_data_q,
                branch="semi_supervised",
                teacher_prediction=teacher_pred, )

            for key in loss_unsup_dict.keys():
                if key[-6:] == "pseudo":
                    loss_unsup_dict[key] = loss_unsup_dict[key] * pseudo_weight
            losses_unsup = paddle.add_n(list(loss_unsup_dict.values()))
            # norm loss when using gradient accumulation
            losses_unsup = losses_unsup / self.cfg.optimize_rate
            losses_unsup.backward()

            loss_dict.update(loss_unsup_dict)
            loss_dict["loss_tot"] += losses_unsup
        return loss_dict

    def export(self, output_dir='output_inference'):
        self.model.eval()
        model_name = os.path.splitext(os.path.split(self.cfg.filename)[-1])[0]
        save_dir = os.path.join(output_dir, model_name)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        image_shape = None
        if self.cfg.architecture in MOT_ARCH:
            test_reader_name = 'TestMOTReader'
        else:
            test_reader_name = 'TestReader'
        if 'inputs_def' in self.cfg[test_reader_name]:
            inputs_def = self.cfg[test_reader_name]['inputs_def']
            image_shape = inputs_def.get('image_shape', None)
        # set image_shape=[3, -1, -1] as default
        if image_shape is None:
            image_shape = [3, -1, -1]

        self.model.modelTeacher.eval()
        if hasattr(self.model.modelTeacher, 'deploy'):
            self.model.modelTeacher.deploy = True

        # Save infer cfg
        _dump_infer_config(self.cfg,
                           os.path.join(save_dir, 'infer_cfg.yml'), image_shape,
                           self.model.modelTeacher)

        input_spec = [{
            "image": InputSpec(
                shape=[None] + image_shape, name='image'),
            "im_shape": InputSpec(
                shape=[None, 2], name='im_shape'),
            "scale_factor": InputSpec(
                shape=[None, 2], name='scale_factor')
        }]
        if self.cfg.architecture == 'DeepSORT':
            input_spec[0].update({
                "crops": InputSpec(
                    shape=[None, 3, 192, 64], name='crops')
            })

        static_model = paddle.jit.to_static(
            self.model.modelTeacher, input_spec=input_spec)
        # NOTE: dy2st do not pruned program, but jit.save will prune program
        # input spec, prune input spec here and save with pruned input spec
        pruned_input_spec = _prune_input_spec(input_spec,
                                              static_model.forward.main_program,
                                              static_model.forward.outputs)

        # dy2st and save model
        if 'slim' not in self.cfg or self.cfg['slim_type'] != 'QAT':
            paddle.jit.save(
                static_model,
                os.path.join(save_dir, 'model'),
                input_spec=pruned_input_spec)
        else:
            self.cfg.slim.save_quantized_model(
                self.model.modelTeacher,
                os.path.join(save_dir, 'model'),
                input_spec=pruned_input_spec)
        logger.info("Export model and saved in {}".format(save_dir))

    def _eval_with_loader(self, loader, mode="teacher"):
        sample_num = 0
        tic = time.time()
        self._compose_callback.on_epoch_begin(self.status)
        self.status['mode'] = 'eval'
        # self.model.eval()
        self.model.modelTeacher.eval()
        self.model.modelStudent.eval()
        for step_id, data in enumerate(loader):
            self.status['step_id'] = step_id
            self._compose_callback.on_step_begin(self.status)
            if mode == "teacher":
                outs = self.model.modelTeacher(data)
            else:
                outs = self.model.modelStudent(data)

            # update metrics
            for metric in self._metrics:
                metric.update(data, outs)

            sample_num += data['im_id'].numpy().shape[0]
            self._compose_callback.on_step_end(self.status)

        self.status['sample_num'] = sample_num
        self.status['cost_time'] = time.time() - tic

        # accumulate metric to log out
        for metric in self._metrics:
            metric.accumulate()
            metric.log()
        self._compose_callback.on_epoch_end(self.status)
        # reset metric states for metric may performed multiple times
        self._reset_metrics()

    def evaluate(self):
        with paddle.no_grad():
            self._eval_with_loader(self.loader)

    @paddle.no_grad()
    def _update_teacher_model(self, keep_rate=0.996):
        student_model_dict = copy.deepcopy(self.model.modelStudent.state_dict())
        new_teacher_dict = dict()
        for key, value in self.model.modelTeacher.state_dict().items():
            if key in student_model_dict.keys():
                v = student_model_dict[key] * (1 - keep_rate
                                               ) + value * keep_rate
                v.stop_gradient = True
                new_teacher_dict[key] = v
            else:
                raise Exception("{} is not found in student model".format(key))

        self.model.modelTeacher.set_dict(new_teacher_dict)


class EnsembleTSModel(nn.Layer):
    def __init__(self, modelTeacher, modelStudent):
        super(EnsembleTSModel, self).__init__()
        self.modelTeacher = modelTeacher
        self.modelStudent = modelStudent
