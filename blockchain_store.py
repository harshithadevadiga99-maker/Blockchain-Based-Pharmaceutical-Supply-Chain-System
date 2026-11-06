import json
import os
from blockchain import Block, Blockchain

BLOCKCHAIN_FILE = 'instance/blockchain.json'


class BlockchainStore:
    def __init__(self):
        os.makedirs('instance', exist_ok=True)
        self.blockchain = self.load()

    def load(self):
        bc = Blockchain()
        if os.path.exists(BLOCKCHAIN_FILE):
            try:
                with open(BLOCKCHAIN_FILE, 'r') as f:
                    data = json.load(f)
                bc.chain = []
                for b in data.get('chain', []):
                    blk = Block(
                        index=b['index'],
                        timestamp=b['timestamp'],
                        event_type=b['event_type'],
                        event_data=b['event_data'],
                        previous_hash=b['previous_hash'],
                        nonce=b.get('nonce', 0)
                    )
                    blk.hash = b['hash']
                    bc.chain.append(blk)
                if not bc.chain:
                    bc.create_genesis_block()
            except Exception as e:
                print(f"Blockchain load error: {e}")
                bc = Blockchain()
        return bc

    def save(self):
        with open(BLOCKCHAIN_FILE, 'w') as f:
            json.dump({'chain': self.blockchain.get_chain_as_list()}, f, indent=2)

    def add_event(self, event_type, event_data):
        block = self.blockchain.add_block(event_type, event_data)
        self.save()
        return block

    def get_all_blocks(self):
        return self.blockchain.get_chain_as_list()

    def verify(self):
        return self.blockchain.is_chain_valid()


_blockchain_store = None


def get_blockchain():
    global _blockchain_store
    if _blockchain_store is None:
        _blockchain_store = BlockchainStore()
    return _blockchain_store