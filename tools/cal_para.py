import os
import sys
# 添加项目根目录到 Python 路径
project_root = os.path.abspath(os.path.join(__file__, '../..'))
sys.path.insert(0, project_root)

import paddle
from ppdet.core.workspace import load_config, merge_config
from ppdet.core.workspace import create


cfg_path = '/home/zwf/Codes/GM-DETR/GM-DETR_paddle/configs/emdetr/gmdetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train2.yml'
# cfg_path = '/home/zwf/Codes/GM-DETR/GM-DETR_paddle/configs/gmdetr/gmdetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train2.yml'
# cfg_path = './configs/rtdetr/rtdetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_random_480-800_x6x6crossrespcat33_mask1swap_ori_rt.yml'
# cfg_path = './configs/rtdetr/rtdetr_hgnetv2_x_5x_align_flir_v1_class3_ir_random_480-800_ori_rt.yml'
cfg = load_config(cfg_path)
model = create(cfg.architecture)

blob = {
    'image': paddle.randn([1, 3, 640, 640]),
    'image_1': paddle.randn([1, 3, 640, 640]),
    'im_shape': paddle.to_tensor([[640, 640]]),
    'scale_factor': paddle.to_tensor([[1., 1.]])
}

paddle.flops(model, None, blob, custom_ops=None, print_detail=False)