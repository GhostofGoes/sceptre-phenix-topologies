#!/bin/bash
filebeat setup -e
filebeat setup --index-management -E output.logstash.enabled=false -E 'output.elasticsearch.hosts=["192.168.60.55:9200"]'
