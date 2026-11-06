import hashlib
import json
from datetime import datetime


class Block:
    def __init__(self, index, timestamp, event_type, event_data, previous_hash, nonce=0):
        self.index = index
        self.timestamp = timestamp
        self.event_type = event_type
        self.event_data = event_data
        self.previous_hash = previous_hash
        self.nonce = nonce
        self.hash = self.calculate_hash()

    def calculate_hash(self):
        block_string = json.dumps({
            'index': self.index,
            'timestamp': str(self.timestamp),
            'event_type': self.event_type,
            'event_data': self.event_data,
            'previous_hash': self.previous_hash,
            'nonce': self.nonce
        }, sort_keys=True)
        return hashlib.sha256(block_string.encode()).hexdigest()

    def mine_block(self, difficulty=2):
        target = '0' * difficulty
        while self.hash[:difficulty] != target:
            self.nonce += 1
            self.hash = self.calculate_hash()
        return self.hash

    def to_dict(self):
        return {
            'index': self.index,
            'timestamp': str(self.timestamp),
            'event_type': self.event_type,
            'event_data': self.event_data,
            'previous_hash': self.previous_hash,
            'nonce': self.nonce,
            'hash': self.hash
        }


class Blockchain:
    DIFFICULTY = 2

    def __init__(self):
        self.chain = []
        self.create_genesis_block()

    def create_genesis_block(self):
        genesis = Block(
            index=0,
            timestamp=datetime.utcnow(),
            event_type='GENESIS',
            event_data={'message': 'Medicine Supply Chain Genesis Block'},
            previous_hash='0' * 64
        )
        genesis.mine_block(self.DIFFICULTY)
        self.chain.append(genesis)

    def get_latest_block(self):
        return self.chain[-1]

    def add_block(self, event_type, event_data):
        previous_block = self.get_latest_block()
        new_block = Block(
            index=previous_block.index + 1,
            timestamp=datetime.utcnow(),
            event_type=event_type,
            event_data=event_data,
            previous_hash=previous_block.hash
        )
        new_block.mine_block(self.DIFFICULTY)
        self.chain.append(new_block)
        return new_block

    def is_chain_valid(self):
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]
            if current.hash != current.calculate_hash():
                return False, f'Block #{current.index} hash is invalid (tampered)'
            if current.previous_hash != previous.hash:
                return False, f'Block #{current.index} not linked to previous block'
            if current.hash[:self.DIFFICULTY] != '0' * self.DIFFICULTY:
                return False, f'Block #{current.index} PoW invalid'
        return True, 'Blockchain is valid'

    def verify_block(self, index):
        if index < 0 or index >= len(self.chain):
            return False, 'Block not found'
        block = self.chain[index]
        if block.hash != block.calculate_hash():
            return False, 'Hash mismatch - tampered'
        if index > 0 and block.previous_hash != self.chain[index - 1].hash:
            return False, 'Chain broken at this block'
        return True, 'Block verified'

    def get_chain_as_list(self):
        return [block.to_dict() for block in self.chain]

    def get_blocks_for_order(self, order_id):
        result = []
        for block in self.chain:
            if isinstance(block.event_data, dict) and block.event_data.get('order_id') == order_id:
                result.append(block)
        return result