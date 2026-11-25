##! Malcolm bind-mounts this directory over zeek/custom/ (read-only), which hides
##! the image's own empty __load__.zeek: without one, `zeek -r pcap local` dies
##! with "can't open .../custom/__load__.zeek" and writes no logs (the malcolm
##! spike's finding). This file is the replacement, and loads our scripts.
@load ./cherrymountain
