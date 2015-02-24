from pyretic.core.language import *
import json, subprocess, netaddr

def mk_filter(pred):
  return { "type": "filter", "pred": pred }

def mk_test(hv):
  return { "type": "test", "header": hv["header"], "value": hv["value"] }

def mk_mod(hv):

  return { "type": "mod", "header": hv["header"], "value": hv["value"] }

def mk_header(h, v):
  return { "header": h, "value": v }


def to_int(bytes):
  n = 0
  for b in bytes:
    n = (n << 8) + ord(b)
  # print "Ethernet: %s -> %s" % (bytes, n)
  return n

def unip(v):
  # if isinstance(v, IPAddr):
  #   bytes = v.bits
  #   n = 0
  #   for b in bytes:
  #     n = (n << 8) + ord(b)
  #   print "IPAddr: %s -> %s (len = %s) -> %s" % (v, bytes, len(bytes), n)
  #   return { "addr": n, "mask": 32 }
  if isinstance(v, netaddr.IPAddress):
    return { "addr": str(v), "mask": 32 }
  else:
    raise TypeError(v)

def unethaddr(v):
  return repr(v)

def physical(n):
  return { "type": "physical", "port": n }

def header_val(h, v):
  if h == "switch":
    return mk_header("switch", v)
  elif h == "inport" or h == "outport":
    return mk_header("location", physical(v))
  elif h == "srcmac":
    return mk_header("ethsrc", unethaddr(v))
  elif h == "dstmac":
    return mk_header("ethdst", unethaddr(v))
  elif h == "vlan_id":
    return mk_header("vlan", v)
  elif h == "vlan_pcp":
    return mk_header("vlanpcp", v)
  elif h == "ethtype":
    return mk_header("ethtype", v)
  elif h == "protocol":
    return mk_header("inproto", v)
  elif h == "srcip":
    return mk_header("ip4src", unip(v))
  elif h == "dstip":
    return mk_header("ip4dst", unip(v))
  elif h == "srcport":
    return mk_header("tcpsrcport", v)
  elif h == "dstport":
    return mk_header("tcpdstport", v)
  else:
    raise TypeError("bad header %s" % h)

def match_to_pred(m):
  lst = [mk_test(header_val(h, m[h])) for h in m]
  return mk_and(lst)

def mod_to_pred(m):
  lst = [ mk_mod(header_val(h, m[h])) for h in m ]
  return mk_seq(lst)


def to_pred(p):
  if isinstance(p, match):
    return match_to_pred(_match(**p.map))
  elif p == identity:
    return { "type": "true" }
  elif p == drop:
    return { "type": "false" }
  elif isinstance(p, negate):
    # Only policies[0] is used in Pyretic
    return { "type": "neg", "pred": to_pred(p.policies[0]) }
  elif isinstance(p, union) or isinstance(p, parallel):
    return mk_or(map(to_pred, p.policies))
  elif isinstance(p, intersection):
    return mk_and(map(to_pred, p.policies))
  else:
    raise TypeError(p)

# TODO(arjun): Consider using aspects to inject methods into each class. That
# would be better object-oriented style.
def to_pol(p):
  if isinstance(p, match):
    return mk_filter(to_pred(p))
  elif p == identity:
    return mk_filter({ "type": "true" })
  elif p == drop:
    return mk_filter({ "type": "false" })
  elif isinstance(p, modify):
    return mod_to_pred(_modify(**p.map).map)
  elif isinstance(p, negate):
    return mk_filter(to_pred(p))
  elif isinstance(p, union):
    return mk_filter(to_pred(p))
  elif isinstance(p, parallel):
    return mk_union(map(to_pol, p.policies))
  #elif isinstance(p, disjoint):
    #return mk_disjoint(map(to_pol, p.policies))
  elif isinstance(p, intersection):
    return mk_filter(to_pred(p))
  elif isinstance(p, sequential):
    return mk_seq(map(to_pol, p.policies))
  elif isinstance(p, fwd):
    return mk_mod(mk_header("location", physical(p.outport)))
  elif isinstance(p, if_):
    c = to_pred(p.pred)
    return mk_union([mk_seq([mk_filter(c), to_pol(p.t_branch)]),
                     mk_seq([mk_filter({ "type": "neg", "pred": c }), to_pol(p.f_branch)])])    
  elif isinstance(p, FwdBucket):
      return {"type" : "mod", "header" : "location", "value": {"type" : "pipe", "name" : str(id(p))}}
  elif isinstance(p, ingress_network) or isinstance(p, egress_network):
      return to_pol(p.policy)
  else:
    raise TypeError("unknown policy %s" % p)

def mk_union(pols):
  return { "type": "union", "pols": pols }

def mk_disjoint(pols):
  return { "type": "disjoint", "pols": pols }

def mk_seq(pols):
  return { "type": "seq", "pols": pols }

def mk_and(preds):
  return { "type": "and", "preds": preds }

def mk_or(preds):
  return { "type": "or", "preds": preds }

# Converts a Pyretic policy into NetKAT, represented
# as a JSON string.
def compile_to_netkat(pyretic_pol):
  return json.dumps(to_pol(pyretic_pol))


############## json to policy ###################

field_map = {'dlSrc' : 'srcmac', 'dlDst': 'dstmac', 'dlTyp': 'ethtype', 
                'dlVlan' : 'vlan_id', 'dlVlanPcp' : 'vlan_pcp',
                'nwSrc' : 'srcip', 'nwDst' : 'dstip', 'nwProto' : 'protocol',
                'tpSrc' : 'srcport', 'tpDst' : 'dstport', 'inPort' : 'inport'}

def create_match(pattern, switch_id):
    match_map = {'switch' : switch_id}
    for k,v in pattern.items():
        if v is not None:
            match_map[field_map[k]] = v

    return match(**match_map)

def create_action(action):
    if len(action) == 0:
        return set()
    else:
        res = set()
        for act_list in action:
            mod_dict = {}
            for act in act_list:
                if act[0] == "Modify":
                    hdr_field = act[1][0][3:]
                    if hdr_field == "Vlan" or hdr_field == "VlanPcp":
                        hdr_field = 'dl' + hdr_field
                    else:
                        hdr_field = hdr_field[0].lower() + hdr_field[1:]
                    hdr_field = field_map[hdr_field]
                    value = act[1][1]
                    if hdr_field == 'srcmac' or hdr_field == 'dstmac':
                        value = MAC(value)
                    mod_dict[hdr_field] = value
                elif act[0] == "Output":
                    out_info = act[1]
                    if out_info['type'] == 'physical':
                        mod_dict['outport'] = out_info['port']
                    elif out_info['type'] == 'controller':
                        res.add(Controller)
            if len(mod_dict) > 0:
                res.add(modify(**mod_dict))
    return res
        
def json_to_classifier(fname):
    data = json.load(fname)
    rules = []
    for sw_tbl in data:
        switch_id = sw_tbl['switch_id']
        for rule in sw_tbl['tbl']:
            prio = rule['priority']
            m = create_match(rule['pattern'], switch_id)
            action = create_action(rule['action'])
            rules.append( (prio, Rule(m, action)))
    #rules.sort()
    rules = [v for (k,v) in rules]
    return Classifier(rules)

