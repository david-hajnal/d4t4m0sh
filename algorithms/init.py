from .gop_iframe_drop import process as gop_iframe_drop
from .flow_leaky import process as flow_leaky
from .blockmatch_basic import process as blockmatch_basic

ALGORITHMS = {
    "gop_iframe_drop": gop_iframe_drop,
    "flow_leaky": flow_leaky,
    "blockmatch_basic": blockmatch_basic,
}
