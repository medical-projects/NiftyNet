# -*- coding: utf-8 -*-
import os
import sys
import utilities.misc as util
import utilities.parse_user_params as parse_user_params

sys.stdout = os.fdopen(sys.stdout.fileno(), 'w')


class NetFactory(object):
    @staticmethod
    def create(name):
        if name == "highres3dnet":
            from network.highres3dnet import HighRes3DNet
            return HighRes3DNet
        elif name == "toynet":
            from network.toynet import ToyNet
            return ToyNet
        elif name == "3dunet":
            from network.unet_3d import UNet_3D
            return UNet_3D
        elif name == "vnet":
            from network.vnet import VNet
            return VNet
        elif name == "deepmedic":
            from network.deepmedic import DeepMedic
            return DeepMedic
        elif name == "scalenet":
            from network.scalenet import ScaleNet
            return ScaleNet
        else:
            print("network: \"{}\" not implemented".format(name))
            raise NotImplementedError

if __name__ == "__main__":
    args = parse_user_params.run()
    if util.has_bad_inputs(args):
        sys.exit(-1)
    if not (args.cuda_devices == '""'):
        os.environ["CUDA_VISIBLE_DEVICES"] = args.cuda_devices
        print("set CUDA_VISIBLE_DEVICES env to {}".format(args.cuda_devices))
    is_training = True if args.action == "train" else False
    device_str = "cpu" if (args.action == "train" and args.num_gpus > 1) else "gpu"

    net_class = NetFactory.create(args.net_name)
    net = net_class(args.batch_size,
                    args.image_size,
                    args.label_size,
                    args.num_classes,
                    is_training=is_training,
                    device_str=device_str)
    if is_training:
        import nn.training
        nn.training.run(net, args)
    else:
        import nn.inference
        nn.inference.run(net, args)
