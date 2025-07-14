# 双模态FEAR-DETR推理指南

## 概述

`infer_dual_modal.py` 是专为双模态FEAR-DETR模型设计的推理脚本，支持RGB、红外以及双模态融合三种推理模式。该脚本基于原始的单模态推理代码扩展而来，保持了原有的功能特性。

## 主要特性

- **多模态支持**: 支持RGB单模态、红外单模态和双模态融合推理
- **图像配对**: 自动匹配RGB和红外图像对（基于文件名）
- **切片推理**: 支持大图像的切片推理，适用于小目标检测
- **灵活输入**: 支持单张图片或批量目录推理
- **结果保存**: 支持可视化结果和检测结果的保存

## 使用方法

### 1. 基本命令格式

```bash
python tools/infer_dual_modal.py \
    -c <config_file> \
    -o weights=<model_weights> \
    --modal_type=<rgb|ir|fusion> \
    [其他参数...]
```

### 2. 参数说明

#### 必需参数
- `-c, --config`: 模型配置文件路径
- `-o weights`: 模型权重文件路径
- `--modal_type`: 推理模态类型
  - `rgb`: 仅使用RGB图像
  - `ir`: 仅使用红外图像  
  - `fusion`: 使用RGB和红外图像融合（默认）

#### 输入参数
- `--infer_dir_rgb`: RGB图像目录路径
- `--infer_dir_ir`: 红外图像目录路径
- `--infer_img_rgb`: 单张RGB图像路径（优先级高于目录）
- `--infer_img_ir`: 单张红外图像路径（优先级高于目录）

#### 输出参数
- `--output_dir`: 输出结果保存目录（默认: "output_dual_modal"）
- `--draw_threshold`: 可视化结果的置信度阈值（默认: 0.5）
- `--save_results`: 是否保存检测结果（默认: True）
- `--visualize`: 是否保存可视化结果（默认: True）

#### 切片推理参数
- `--slice_infer`: 启用切片推理
- `--slice_size`: 切片大小，格式: height width（默认: [640, 640]）
- `--overlap_ratio`: 重叠比例，格式: height_ratio width_ratio（默认: [0.25, 0.25]）
- `--combine_method`: 结果合并方法（默认: "nms"）
- `--match_threshold`: 匹配阈值（默认: 0.6）
- `--match_metric`: 匹配指标，"iou"或"ios"（默认: "ios"）

### 3. 使用示例

#### 双模态融合推理（推荐）
```bash
python tools/infer_dual_modal.py \
    -c configs/emdetr/emdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_em/gmdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2/model_final.pdparams \
    --modal_type=fusion \
    --infer_dir_rgb=infer_images/llvip/rgb \
    --infer_dir_ir=infer_images/llvip/ir \
    --output_dir=infer_output/fusion \
    --draw_threshold=0.5
```

#### 单张图片推理
```bash
python tools/infer_dual_modal.py \
    -c configs/emdetr/emdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_em/gmdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2/model_final.pdparams \
    --modal_type=fusion \
    --infer_img_rgb=infer_images/llvip/rgb/sample_001.jpg \
    --infer_img_ir=infer_images/llvip/ir/sample_001.jpg \
    --output_dir=infer_output/single
```

#### RGB单模态推理
```bash
python tools/infer_dual_modal.py \
    -c configs/emdetr/emdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_em/gmdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2/model_final.pdparams \
    --modal_type=rgb \
    --infer_dir_rgb=infer_images/llvip/rgb \
    --output_dir=infer_output/rgb_only
```

#### 红外单模态推理
```bash
python tools/infer_dual_modal.py \
    -c configs/emdetr/emdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_em/gmdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2/model_final.pdparams \
    --modal_type=ir \
    --infer_dir_ir=infer_images/llvip/ir \
    --output_dir=infer_output/ir_only
```

#### 切片推理（适用于大图像）
```bash
python tools/infer_dual_modal.py \
    -c configs/emdetr/emdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_em/gmdetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2/model_final.pdparams \
    --modal_type=fusion \
    --infer_dir_rgb=infer_images/llvip/rgb \
    --infer_dir_ir=infer_images/llvip/ir \
    --slice_infer \
    --slice_size 640 640 \
    --overlap_ratio 0.25 0.25 \
    --output_dir=infer_output/slice
```

## 目录结构要求

### 双模态融合推理
当使用双模态融合推理时，RGB和红外图像应该有相同的文件名（不包含扩展名）：

```
infer_images/
├── rgb/
│   ├── image001.jpg
│   ├── image002.jpg
│   └── ...
└── ir/
    ├── image001.jpg
    ├── image002.jpg
    └── ...
```

### 输出结构
推理结果将保存在指定的输出目录中：

```
output_dir/
├── image001.jpg          # 可视化结果
├── image002.jpg
├── ...
└── bbox.json            # 检测结果（如果启用save_results）
```

## 注意事项

1. **模型配置**: 确保使用的配置文件与双模态模型兼容
2. **权重文件**: 使用训练好的双模态模型权重
3. **图像格式**: 支持jpg、jpeg、png、bmp格式
4. **文件命名**: 双模态融合需要RGB和红外图像有对应的文件名
5. **内存使用**: 切片推理可以减少内存使用，适用于大图像或GPU内存受限的情况

## 快速开始

运行提供的示例脚本：

```bash
chmod +x tools/infer_dual_modal_examples.sh
./tools/infer_dual_modal_examples.sh
```

该脚本包含了多种推理模式的示例，可以根据需要修改路径和参数。

## 与原始推理脚本的区别

与`infer.py`相比，主要增加了以下功能：

1. **多模态输入支持**: 支持RGB、红外和融合三种模式
2. **图像配对机制**: 自动匹配RGB和红外图像对
3. **模态选择**: 可以灵活选择使用哪种模态进行推理
4. **扩展的参数**: 增加了双模态相关的命令行参数

这使得该脚本能够充分利用双模态FEAR-DETR模型的优势，在不同场景下选择最适合的推理模式。
